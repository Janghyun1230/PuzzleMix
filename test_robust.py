#!/usr/bin/env python
from __future__ import division

import os, sys, shutil, time, random
sys.path.append('..')
from glob import glob
import argparse
from distutils.dir_util import copy_tree
from shutil import rmtree
import torch
import torch.backends.cudnn as cudnn
import torch.nn as nn
import torch.nn.functional as F
from torch.autograd import Variable
import torchvision.datasets as dset
import torchvision.transforms as transforms
from torch.utils.data import DataLoader

from utils import *
import models

if sys.version_info[0] < 3:
    import cPickle as pickle
else:
    import _pickle as pickle
import numpy as np
from collections import OrderedDict, Counter
from load_data  import *
from helpers import *


model_names = sorted(name for name in models.__dict__
  if name.islower() and not name.startswith("__")
  and callable(models.__dict__[name]))

parser = argparse.ArgumentParser(description='Trains ResNeXt on CIFAR or ImageNet', formatter_class=argparse.ArgumentDefaultsHelpFormatter)
parser.add_argument('--dataset', type=str, default='cifar100', choices=['cifar10', 'cifar100', 'imagenet', 'svhn', 'stl10', 'mnist', 'tiny-imagenet-200'], help='Choose between Cifar10/100 and ImageNet.')
parser.add_argument('--arch', metavar='ARCH', default='resnext29_8_64', choices=model_names, help='model architecture: ' + ' | '.join(model_names) + ' (default: resnext29_8_64)')
parser.add_argument('--train', default='')
parser.add_argument('--ckpt', default='', type=str, metavar='PATH', help='path to latest checkpoint (default: none)')

args = parser.parse_args()

cudnn.benchmark = True

def accuracy(output, target, topk=(1,)):
    """Computes the precision@k for the specified values of k"""
    maxk = max(topk)
    batch_size = target.size(0)

    _, pred = output.topk(maxk, 1, True, True)
    pred = pred.t()
    correct = pred.eq(target.view(1, -1).expand_as(pred))

    res = []
    for k in topk:
        correct_k = correct[:k].view(-1).float().sum(0)
        res.append(correct_k.mul_(100.0 / batch_size))
    return res


bce_loss = nn.BCELoss().cuda()
softmax = nn.Softmax(dim=1).cuda()
criterion = nn.CrossEntropyLoss().cuda()
mse_loss = nn.MSELoss().cuda()


if args.dataset == 'tiny-imagenet-200':
    stride = 2 
    width = 64 
    num_classes = 200
    mean = 127.5/255
    std = 127.5/255
else:
    stride = 1
    width = 32
    num_classes = 100
    mean = torch.tensor([x / 255 for x in [129.3, 124.1, 112.4]], dtype=torch.float32).view(1,3,1,1).cuda()
    std = torch.tensor([x / 255 for x in [68.2, 65.4, 70.4]], dtype=torch.float32).view(1,3,1,1).cuda()

# Net load
print("=> creating model '{}'".format(args.arch))
net = models.__dict__[args.arch](num_classes, False, False, stride).cuda()
#net = nn.DataParallel(net)
net_best = models.__dict__[args.arch](num_classes, False, False, stride).cuda()
#net_best = nn.DataParallel(net_best)

checkpoint = torch.load(args.ckpt+'/checkpoint.pth.tar')
checkpoint['state_dict'] = dict((key[7:], value) for (key, value) in checkpoint['state_dict'].items())
net.load_state_dict(checkpoint['state_dict'])

checkpoint_best = torch.load(args.ckpt+'/model_best.pth.tar')
checkpoint_best['state_dict'] = dict((key[7:], value) for (key, value) in checkpoint_best['state_dict'].items())
net_best.load_state_dict(checkpoint_best['state_dict'])

recorder = checkpoint['recorder']
best_acc = recorder.max_accuracy(False)
print("=> loaded checkpoint '{}' accuracy={:.2f} (epoch {})" .format(args.ckpt, best_acc, checkpoint['epoch']))


net.eval()
net_best.eval()

# Vanilla Test
test_transform = transforms.Compose([transforms.ToTensor()])
dataset = dset.CIFAR100(root='./data/cifar100', train=False, download=False, transform=test_transform)
testloader = DataLoader(dataset, batch_size=100)

prec1_total = 0
prec5_total = 0
prec1_total_best = 0
prec5_total_best = 0
for batch_idx, (input, target) in enumerate(testloader):
    with torch.no_grad():
        input = input.cuda()
        target = target.cuda()
        
        output = net((input - mean)/std)
        prec1, prec5 = accuracy(output, target, topk=(1,5))
        prec1_total += prec1.item()
        prec5_total += prec5.item()
        
        output = net_best((input - mean)/std)
        prec1, prec5 = accuracy(output, target, topk=(1,5))
        prec1_total_best += prec1.item()
        prec5_total_best += prec5.item()
print("prec1: {:.2f}  prec1_best: {:.2f}   prec5: {:.2f}  prec5_best: {:.2f}".format(prec1_total/100, prec1_total_best/100, prec5_total/100, prec5_total_best/100))


# Input Corruption Test
dataset_cifar100_dist_list = glob('/home/janghyun/Codes/Wasserstein_Preprocessor/manifold_mixup/data/Cifar100-C/*.npy')
label = np.load('/home/janghyun/Codes/Wasserstein_Preprocessor/manifold_mixup/data/Cifar100-C/labels.npy')

for path in dataset_cifar100_dist_list:
    name = os.path.basename(path)[:-4]
    if name == 'labels':
        continue
        
    #print("Distortion: {}".format(name))
    dataset_cifar100_dist = np.load(path)
    dataset_cifar100_dist = dataset_cifar100_dist.reshape(5, 100, 100, 32, 32, 3)
    
    for level in range(5):
        #print("(level{})".format(level+1), end='  ')
        prec1_total = 0
        prec5_total = 0
        prec1_total_best = 0
        prec5_total_best = 0

        for batch_idx, input in enumerate(dataset_cifar100_dist[level]):
            with torch.no_grad():
                input = torch.tensor(input/255, dtype=torch.float32).permute(0,3,1,2).cuda()
                target = torch.tensor(label[batch_idx*100: (batch_idx+1)*100], dtype=torch.int64).cuda()
                
                output = net((input - mean)/std)
                prec1, prec5 = accuracy(output, target, topk=(1,5))
                prec1_total += prec1.item()
                #prec5_total += prec5.item()
                
                #output = net_best((input - mean)/std)
                #prec1, prec5 = accuracy(output, target, topk=(1,5))
                #prec1_total_best += prec1.item()
                #prec5_total_best += prec5.item()

        #print("prec1: {:.2f}  prec1_best: {:.2f}   prec5: {:.2f}  prec5_best: {:.2f}".format(prec1_total/100, prec1_total_best/100, prec5_total/100, prec5_total_best/100))
        print("{:.2f}".format(prec1_total/100))


