# -*- encoding: utf-8 -*-

import torch.nn as nn
from nn_utils import *
from custom_modules import *


class FeatureNet(nn.Module):
    """
        A simple network consisting only of the features extracted
        from an underlying CNN, which can be averaged spatially and
        are then returned as a flat vector
    """
    def __init__(self, net, feature_size2d, average_features=False, classify=False):
        super(FeatureNet, self).__init__()
        self.features, self.feature_reduc, self.classifier = extract_layers(net)
        if not classify:
            self.feature_reduc = nn.Sequential()
            self.classifier = nn.Sequential()
            factor = feature_size2d[0] * feature_size2d[1]
            self.feature_size = get_feature_size(self.features, factor)
            if average_features:
                self.feature_reduc = nn.Sequential(
                    nn.AvgPool2d(feature_size2d)
                )
                self.feature_size /= (feature_size2d[0] * feature_size2d[1])
        else:
            self.feature_size = get_feature_size(self.classifier)
        self.norm = NormalizeL2()

    def forward(self, x):
        x = self.features(x)
        x = self.feature_reduc(x)
        x = x.view(x.size(0), -1)
        x = self.classifier(x)
        x = self.norm(x)
        return x


class TuneClassif(nn.Module):
    """
        Image classification network based on a pretrained network
        which is then finetuned to a different dataset
        It's assumed that the last layer of the given network
        is a fully connected (linear) one
        untrained_blocks specifies how many layers or blocks of layers are
        left untrained (only layers with parameters are counted). for ResNet, each 'BottleNeck' or 'BasicBlock' (block containing skip connection for residual) is considered as one block
    """

    def __init__(self, net, num_classes, untrained_blocks=-1, reduc=True):
        super(TuneClassif, self).__init__()
        self.features, self.feature_reduc, self.classifier = extract_layers(net)
        if untrained_blocks < 0:
            untrained_blocks = sum(1 for _ in self.features) + sum(1 for _ in self.classifier)
        # make sure we never retrain the first few layers
        # this is usually not needed
        seqs = [self.features, self.feature_reduc, self.classifier]

        def has_param(m):
            return sum(1 for _ in m.parameters()) > 0
        count = 0
        for module in (m for seq in seqs for m in seq if has_param(m)):
            if count >= untrained_blocks:
                break
            count += 1
            for p in module.parameters():
                p.requires_grad = False

        # replace last module of classifier with a reduced one
        for name, module in self.classifier._modules.items():
            if module is self.classifier[len(self.classifier._modules) - 1]:
                self.classifier._modules[name] = nn.Linear(module.in_features, num_classes)
        self.feature_size = num_classes
        # if no reduc is wanted, remove it
        if not reduc:
            factor = 1
            for m in self.feature_reduc:
                try:
                    factor *= (m.kernel_size[0] * m.kernel_size[1])
                except TypeError:
                    factor *= m.kernel_size * m.kernel_size
            # increase the number of input features on first classifier module
            for name, module in self.classifier._modules.items():
                if module is self.classifier[0]:
                    self.classifier._modules[name] = nn.Linear(module.in_features * factor, module.out_features)
            self.feature_reduc = nn.Sequential()

    def forward(self, x):
        x = self.features(x)
        x = self.feature_reduc(x)
        x = x.view(x.size(0), -1)
        x = self.classifier(x)
        return x


class Siamese1(nn.Module):
    """
        Define a siamese network
        Given a module, it will duplicate it with weight sharing, concatenate the output and add a linear classifier
        TODO description, feature reduc (resnet was trained for this avgpool)
    """
    def __init__(self, net, feature_dim, feature_size2d, spatial_avg_factor=(1, 1)):
        super(Siamese1, self).__init__()
        self.features, _, classifier = extract_layers(net)
        if spatial_avg_factor[0] == -1:
            spatial_avg_factor[0] = feature_size2d[0]
        if spatial_avg_factor[1] == -1:
            spatial_avg_factor[1] = feature_size2d[1]
        self.spatial_feature_reduc = nn.Sequential(
            nn.AvgPool2d(spatial_avg_factor)
        )
        factor = feature_size2d[0] * feature_size2d[1] / (spatial_avg_factor[0] * spatial_avg_factor[1])
        in_features = get_feature_size(self.features, factor)
        if feature_dim <= 0:
            self.feature_size = get_feature_size(classifier)
        else:
            self.feature_size = feature_dim
        self.feature_reduc1 = nn.Sequential(
            NormalizeL2(),
            Shift(in_features),
            nn.Linear(in_features, self.feature_size)
        )
        self.feature_reduc2 = NormalizeL2()

    def forward_single(self, x):
        x = self.features(x)
        x = self.spatial_feature_reduc(x)
        x = x.view(x.size(0), -1)
        x = self.feature_reduc1(x)
        x = self.feature_reduc2(x)
        return x

    def forward(self, x1, x2=None, x3=None):
        if self.training and x3:
            return self.forward_single(x1), self.forward_single(x2), self.forward_single(x3)
        elif self.training:
            return self.forward_single(x1), self.forward_single(x2)
        else:
            return self.forward_single(x1)
