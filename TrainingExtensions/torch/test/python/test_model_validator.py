# -*- mode: python -*-
# =============================================================================
#  @@-COPYRIGHT-START-@@
#
#  Copyright (c) 2021, Qualcomm Innovation Center, Inc. All rights reserved.
#
#  Redistribution and use in source and binary forms, with or without
#  modification, are permitted provided that the following conditions are met:
#
#  1. Redistributions of source code must retain the above copyright notice,
#     this list of conditions and the following disclaimer.
#
#  2. Redistributions in binary form must reproduce the above copyright notice,
#     this list of conditions and the following disclaimer in the documentation
#     and/or other materials provided with the distribution.
#
#  3. Neither the name of the copyright holder nor the names of its contributors
#     may be used to endorse or promote products derived from this software
#     without specific prior written permission.
#
#  THIS SOFTWARE IS PROVIDED BY THE COPYRIGHT HOLDERS AND CONTRIBUTORS "AS IS"
#  AND ANY EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT LIMITED TO, THE
#  IMPLIED WARRANTIES OF MERCHANTABILITY AND FITNESS FOR A PARTICULAR PURPOSE
#  ARE DISCLAIMED. IN NO EVENT SHALL THE COPYRIGHT HOLDER OR CONTRIBUTORS BE
#  LIABLE FOR ANY DIRECT, INDIRECT, INCIDENTAL, SPECIAL, EXEMPLARY, OR
#  CONSEQUENTIAL DAMAGES (INCLUDING, BUT NOT LIMITED TO, PROCUREMENT OF
#  SUBSTITUTE GOODS OR SERVICES; LOSS OF USE, DATA, OR PROFITS; OR BUSINESS
#  INTERRUPTION) HOWEVER CAUSED AND ON ANY THEORY OF LIABILITY, WHETHER IN
#  CONTRACT, STRICT LIABILITY, OR TORT (INCLUDING NEGLIGENCE OR OTHERWISE)
#  ARISING IN ANY WAY OUT OF THE USE OF THIS SOFTWARE, EVEN IF ADVISED OF THE
#  POSSIBILITY OF SUCH DAMAGE.
#
#  SPDX-License-Identifier: BSD-3-Clause
#
#  @@-COPYRIGHT-END-@@
# =============================================================================

import pytest
import unittest
import torch
import torch.nn.functional as F

from aimet_torch import utils
from aimet_torch.model_validator.model_validator import ModelValidator
from aimet_torch.model_validator import validation_checks
from aimet_torch.examples import test_models
from aimet_torch.meta import connectedgraph_utils
from aimet_torch.model_preparer import prepare_model


class CustomModule(torch.nn.Module):

    @staticmethod
    def forward(x):
        return x * F.softplus(x).sigmoid()


class Model(torch.nn.Module):
    """ Model that uses functional modules instead of nn.Modules. Expects input of shape (1, 3, 32, 32) """

    def __init__(self):
        super(Model, self).__init__()
        self.conv1 = torch.nn.Conv2d(3, 8, kernel_size=2, stride=2, padding=2, bias=False)
        self.bn1 = torch.nn.BatchNorm2d(8)
        self.relu1 = torch.nn.ReLU(inplace=True)
        self.custom = CustomModule()

    def forward(self, inputs):
        x = self.conv1(inputs)
        x = self.relu1(x)
        x = self.bn1(x)
        x = F.relu(x)
        x = self.relu1(x)
        x = self.custom(x)
        x += 1
        x = torch.flatten(x)
        return x


class TestValidateModel(unittest.TestCase):
    """ Class for testing model validator """

    def test_model_validator(self):
        """ Check that model validator returns correct value """

        model = test_models.SequentialModel()
        rand_inp = torch.randn(1, 3, 8, 8)
        self.assertTrue(ModelValidator.validate_model(model, rand_inp))

        model = test_models.ModelWithReusedNodes()
        rand_inp = torch.randn(1, 3, 32, 32)
        self.assertFalse(ModelValidator.validate_model(model, rand_inp))


class TestValidationChecks(unittest.TestCase):
    """ Class for testing validation check functions """

    def test_validate_for_reused_modules(self):
        """ Validate the check for reused modules """

        model = test_models.ModelWithReusedNodes()
        rand_inp = torch.randn(1, 3, 32, 32)
        self.assertFalse(validation_checks.validate_for_reused_modules(model, rand_inp))

    def test_validate_for_missing_modules(self):
        """ Validate the check for ops with missing modules """

        model = test_models.ModelWithFunctionalOps()
        rand_inp = torch.randn(1, 3, 32, 32)
        self.assertFalse(validation_checks.validate_for_missing_modules(model, rand_inp))

    def test_get_filtered_ops_with_missing_modules(self):
        """ Validate the missing modules check with excluded layers """
        model = Model().eval()
        input_shape = (1, 3, 32, 32)
        dummy_input = torch.rand(input_shape)
        layers_to_exclude = [model.custom]
        filtered_ops_with_missing_modules = \
            validation_checks._get_filtered_ops_with_missing_modules(model, dummy_input,
                                                                     layers_to_exclude=layers_to_exclude)

        # Check that only two ops (addition and relu) are flagged
        self.assertEqual(2, len(filtered_ops_with_missing_modules))

    def test_get_blacklisted_modules(self):
        """ Test get_blacklisted_modules utility """
        model = test_models.HierarchicalModel()
        layers_to_exclude = {model.nm1, model.nm2}
        blacklisted_layers = validation_checks._get_blacklisted_layers(layers_to_exclude)
        manually_obtained_set = set()
        for module in model.nm1.modules():
            manually_obtained_set.add(module)
        for module in model.nm2.modules():
            manually_obtained_set.add(module)
        self.assertEqual(manually_obtained_set, blacklisted_layers)


class TestModelValidatorPreparer:

    @pytest.mark.cuda
    @pytest.mark.skip('Disable temporarily while CG does not dive into custom modules with only functionals.')
    def test_model_validator_preparer(self):
        """ Validate model validator and preparer workflow """
        model = Model().eval().cuda()
        input_shape = (1, 3, 32, 32)
        dummy_input = torch.rand(input_shape).cuda()

        # ModelValidator check should be False before.
        assert not ModelValidator.validate_model(model, dummy_input)

        reused_modules = utils.get_reused_modules(model, dummy_input)
        print(reused_modules)
        assert len(reused_modules) == 1

        ops_with_missing_modules = connectedgraph_utils.get_ops_with_missing_modules(model, dummy_input)
        print(ops_with_missing_modules)
        assert len(ops_with_missing_modules) == 5 # ['relu_3', 'softplus_5', 'sigmoid_6', 'Mul_7', 'Add_8']

        # Prepare model and verify the outputs.
        prepared_model = prepare_model(model)
        assert torch.equal(model(dummy_input), prepared_model(dummy_input))

        # ModelValidator check should be True after.
        assert ModelValidator.validate_model(prepared_model, dummy_input)
