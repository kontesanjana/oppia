# coding: utf-8
#
# Copyright 2013 Google Inc. All Rights Reserved.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#      http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS-IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Interface for storage model switching."""

__author__ = 'Sean Lip'

import feconf
import utils

# Valid model names.
NAMES = utils.create_enum(
    'base_model', 'exploration', 'image', 'parameter', 'state', 'statistics')


class _Platform(object):
    @classmethod
    def import_models(cls):
        raise NotImplementedError


class _Django(_Platform):

    @classmethod
    def import_models(cls):
        # TODO(sunu/sll): Implement this method.
        raise NotImplementedError

    NAME = 'django'


class _Gae(_Platform):
    @classmethod
    def import_models(cls, model_names):

        returned_models = []
        for name in model_names:
            if name == NAMES.base_model:
                from oppia.storage.base_model import models as base_model
                returned_models.append(base_model)
            elif name == NAMES.exploration:
                from oppia.storage.exploration import models as exp_model
                returned_models.append(exp_model)
            elif name == NAMES.image:
                from oppia.storage.image import models as image_model
                returned_models.append(image_model)
            elif name == NAMES.parameter:
                from oppia.storage.parameter import models as parameter_model
                returned_models.append(parameter_model)
            elif name == NAMES.state:
                from oppia.storage.state import models as state_model
                returned_models.append(state_model)
            elif name == NAMES.statistics:
                from oppia.storage.statistics import models as statistics_model
                returned_models.append(statistics_model)
            else:
                raise Exception('Invalid model name: %s' % name)

        return tuple(returned_models)

    NAME = 'gae'


class Registry(object):
    _PLATFORM_MAPPING = {
        _Django.NAME: _Django,
        _Gae.NAME: _Gae,
    }

    @classmethod
    def _get(cls):
        return cls._PLATFORM_MAPPING.get(feconf.PLATFORM)

    @classmethod
    def import_models(cls, model_names):
        return cls._get().import_models(model_names)
