#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Created on Wed Nov 17 02:08:09 2021

@author: root
"""

import torch
import torch.nn as nn
from nets.operations import *


def drop_path(x, drop_prob):
  if drop_prob > 0.:
    keep_prob = 1. - drop_prob
    mask = torch.cuda.FloatTensor(x.size(0), 1, 1, 1).bernoulli_(keep_prob)
    x.div_(keep_prob)
    x.mul_(mask)
  return x


class Cell(nn.Module):

  def __init__(self, genotype, C_prev_prev, C_prev, C, reduction, reduction_prev):
    """
    :param genotype:
    :param C_prev_prev:
    :param C_prev:
    :param C:
    :param reduction:
    :param reduction_prev:
    """
    super(Cell, self).__init__()

    print(C_prev_prev, C_prev, C)

    if reduction_prev:
      self.preprocess0 = FactorizedReduce(C_prev_prev, C)
    else:
      self.preprocess0 = ReLUConvBN(C_prev_prev, C, kernel_size=1, stride=1, padding=0)
    self.preprocess1 = ReLUConvBN(C_prev, C, kernel_size=1, stride=1, padding=0)

    if reduction:
      op_names, indices = zip(*genotype.reduce)
      concat = genotype.reduce_concat
    else:
      op_names, indices = zip(*genotype.normal)
      concat = genotype.normal_concat

    assert len(op_names) == len(indices)

    self._num_nodes = len(op_names) // 2
    self._concat = concat
    self.multiplier = len(concat)

    self._ops = nn.ModuleList()
    for name, index in zip(op_names, indices):
      stride = 2 if reduction and index < 2 else 1
      op = OPS[name](C, stride, affine=True)
      self._ops += [op]
    self._indices = indices

  def forward(self, s0, s1, drop_prob):
    """
    :param s0:
    :param s1:
    :param drop_prob:
    :return:
    """
    s0 = self.preprocess0(s0)
    s1 = self.preprocess1(s1)

    states = [s0, s1]
    for i in range(self._num_nodes):
      h1 = states[self._indices[2 * i]]
      h2 = states[self._indices[2 * i + 1]]
      op1 = self._ops[2 * i]
      op2 = self._ops[2 * i + 1]
      h1 = op1(h1)
      h2 = op2(h2)

      if self.training and drop_prob > 0.:
        if not isinstance(op1, Identity):
          h1 = drop_path(h1, drop_prob)
        if not isinstance(op2, Identity):
          h2 = drop_path(h2, drop_prob)

      s = (h1 + h2) / 2
      states += [s]
    return torch.cat([states[i] for i in self._concat], dim=1)


class AuxiliaryHeadCIFAR(nn.Module):

  def __init__(self, C, num_classes):
    """assuming input size 8x8"""
    super(AuxiliaryHeadCIFAR, self).__init__()

    self.features = nn.Sequential(
      nn.ReLU(inplace=True),
      nn.AvgPool2d(5, stride=3, padding=0, count_include_pad=False),  # image size = 2 x 2
      nn.Conv2d(C, 128, kernel_size=1, bias=False),
      nn.BatchNorm2d(128),
      nn.ReLU(inplace=True),
      nn.Conv2d(128, 768, kernel_size=2, bias=False),
      nn.BatchNorm2d(768),
      nn.ReLU(inplace=True)
    )
    self.classifier = nn.Linear(768, num_classes)

  def forward(self, x):
    x = self.features(x)
    x = self.classifier(x.view(x.size(0), -1))
    return x


class NetworkCIFAR(nn.Module):

  def __init__(self, genotype, C, layers, auxiliary, num_classes):
    super(NetworkCIFAR, self).__init__()
    self.drop_path_prob = 0.0
    self._layers = layers
    self._auxiliary = auxiliary

    stem_multiplier = 3
    C_curr = stem_multiplier * C
    self.stem = nn.Sequential(nn.Conv2d(3, C_curr, kernel_size=3, padding=1, bias=False),
                              nn.BatchNorm2d(C_curr))

    C_prev_prev, C_prev, C_curr = C_curr, C_curr, C
    self.cells = nn.ModuleList()
    reduction_prev = False
    for i in range(layers):
      if i in [layers // 3, 2 * layers // 3]:
        C_curr *= 2
        reduction = True
      else:
        reduction = False
      cell = Cell(genotype, C_prev_prev, C_prev, C_curr, reduction, reduction_prev)
      reduction_prev = reduction
      self.cells += [cell]
      C_prev_prev, C_prev = C_prev, cell.multiplier * C_curr
      if i == 2 * layers // 3:
        C_to_auxiliary = C_prev

    if auxiliary:
      self.auxiliary_head = AuxiliaryHeadCIFAR(C_to_auxiliary, num_classes)
    self.global_pooling = nn.AdaptiveAvgPool2d(1)
    self.classifier = nn.Linear(C_prev, num_classes)

  def forward(self, input):
    logits_aux = None
    s0 = s1 = self.stem(input)
    for i, cell in enumerate(self.cells):
      s0, s1 = s1, cell(s0, s1, self.drop_path_prob)
      if i == 2 * self._layers // 3:
        if self._auxiliary and self.training:
          logits_aux = self.auxiliary_head(s1)
    out = self.global_pooling(s1)
    logits = self.classifier(out.view(out.size(0), -1))
    return logits, logits_aux





# if __name__ == '__main__':
#   import os
#   import pickle
#   from genotypes import *
#   from utils.utils import count_flops, count_parameters
#
#   os.environ["CUDA_DEVICE_ORDER"] = "PCI_BUS_ID"  # see issue #152
#   os.environ["CUDA_VISIBLE_DEVICES"] = '0'
#
#
#   def hook(self, input, output):
#     print(output.data.cpu().numpy().shape)
#     pass
#
#
#   genotype = Genotype(normal=[('dil_conv_5x5', 0), ('skip_connect', 1),
#                               ('sep_conv_3x3', 0), ('sep_conv_3x3', 1),
#                               ('sep_conv_3x3', 0), ('sep_conv_5x5', 2),
#                               ('sep_conv_3x3', 0), ('dil_conv_5x5', 3)],
#                       normal_concat=range(2, 6),
#                       reduce=[('max_pool_3x3', 0), ('max_pool_3x3', 1),
#                               ('sep_conv_3x3', 0), ('avg_pool_3x3', 1),
#                               ('dil_conv_3x3', 3), ('sep_conv_3x3', 0),
#                               ('avg_pool_3x3', 1), ('max_pool_3x3', 0)],
#                       reduce_concat=range(2, 6))
#
#   net = NetworkCIFAR(genotype=genotype, C=36, layers=20, auxiliary=0.4, num_classes=10)
#
#   for m in net.modules():
#     if isinstance(m, nn.Conv2d):
#       m.register_forward_hook(hook)
#
#   y = net(torch.randn(2, 3, 32, 32))
#   print(y[0].size())
#
#   count_parameters(net)
#   count_flops(net, input_size=32)