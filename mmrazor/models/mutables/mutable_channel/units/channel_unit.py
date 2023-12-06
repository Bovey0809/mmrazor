# Copyright (c) OpenMMLab. All rights reserved.
import copy
from typing import Dict, List

import torch.nn as nn
from mmengine.model import BaseModule

from mmrazor.models.architectures.dynamic_ops.mixins import DynamicChannelMixin
from mmrazor.registry import TASK_UTILS


class Channel(BaseModule):
    """Channel records information about channels for pruning.

    Args:
        name (str): The name of the channel. When the channel is related with
            a module, the name should be the name of the module in the model.
        module (Any): Module of the channel.
        index (Tuple[int,int]): Index(start,end) of the Channel in the Module
        node (ChannelNode, optional): A ChannelNode corresponding to the
            Channel. Defaults to None.
        is_output_channel (bool, optional): Is the channel output channel.
            Defaults to True.
    """

    # init

    def __init__(self,
                 name,
                 module,
                 index,
                 node=None,
                 is_output_channel=True) -> None:
        super().__init__()
        self.name = name
        self.module: nn.Module = module
        self.index = index
        self.start = index[0]
        self.end = index[1]

        self.node = node

        self.is_output_channel = is_output_channel

    @classmethod
    def init_from_cfg(cls, model: nn.Module, config: Dict):
        """init a Channel using a config which can be generated by
        self.config_template()"""
        name = config['name']
        start = config['start']
        end = config['end']
        is_output_channel = config['is_output_channel']

        name2module = dict(model.named_modules())
        name2module.pop('')
        module = name2module.get(name, None)
        return Channel(
            name, module, (start, end), is_output_channel=is_output_channel)

    # config template

    def config_template(self):
        """Generate a config template which can be used to initialize a Channel
        by cls.init_from_cfg(**kwargs)"""

        return {
            'name': str(self.name),
            'start': self.start,
            'end': self.end,
            'is_output_channel': self.is_output_channel
        }

    # basic properties

    @property
    def num_channels(self) -> int:
        """The number of channels in the Channel."""
        return self.index[1] - self.index[0]

    @property
    def is_mutable(self) -> bool:
        """If the channel is prunable."""
        if self.module is not None:
            has_prama = len(list(self.module.parameters())) != 0
            is_dynamic_op = isinstance(self.module, DynamicChannelMixin)
            return (not has_prama) or is_dynamic_op
        else:
            is_unmutable = self.name in [
                'input_placeholder', 'output_placeholder'
            ]
            return not is_unmutable

    def __repr__(self) -> str:
        return (f'{self.__class__.__name__}('
                f'{self.name}, index={self.index}, '
                f'is_output_channel='
                f'{"true" if self.is_output_channel else "false"}, '
                ')')

    def __eq__(self, obj: object) -> bool:
        if isinstance(obj, Channel):
            return self.name == obj.name \
                and self.module == obj.module \
                and self.index == obj.index \
                and self.is_output_channel == obj.is_output_channel \
                and self.node == obj.node
        else:
            return False


# Channel && ChannelUnit


class ChannelUnit(BaseModule):
    """A unit of Channels.

    A ChannelUnit has two list, input_related and output_related, to store
    the Channels. These Channels are dependent on each other, and have to
    have the same number of activated number of channels.

    Args:
        num_channels (int): the number of channels of Channel object.
    """

    # init methods

    def __init__(self, num_channels: int, **kwargs):
        super().__init__()

        self.num_channels = num_channels
        self.output_related: List[nn.Module] = []
        self.input_related: List[nn.Module] = []
        self.init_args: Dict = {
        }  # is used to generate new channel unit with same args

    @classmethod
    def init_from_cfg(cls, model: nn.Module, config: Dict) -> 'ChannelUnit':
        """init a ChannelUnit using a config which can be generated by
        self.config_template()"""

        def auto_fill_channel_config(channel_config: Dict,
                                     is_output_channel: bool,
                                     unit_config: Dict = config):
            """Fill channel config with default values."""
            if 'start' not in channel_config:
                channel_config['start'] = 0
            if 'end' not in channel_config:
                channel_config['end'] = unit_config['init_args'][
                    'num_channels']
            channel_config['is_output_channel'] = is_output_channel

        config = copy.deepcopy(config)
        channels = config.pop('channels') if 'channels' in config else None
        unit = cls(**(config['init_args']))
        if channels is not None:
            for channel_config in channels['input_related']:
                auto_fill_channel_config(channel_config, False)
                unit.add_input_related(
                    Channel.init_from_cfg(model, channel_config))
            for channel_config in channels['output_related']:
                auto_fill_channel_config(channel_config, True)
                unit.add_output_related(
                    Channel.init_from_cfg(model, channel_config))
        return unit

    @classmethod
    def init_from_channel_unit(cls,
                               unit: 'ChannelUnit',
                               args: Dict = {}) -> 'ChannelUnit':
        """Initial a object of current class from a ChannelUnit object."""
        args['num_channels'] = unit.num_channels
        mutable_unit = cls(**args)
        mutable_unit.input_related = unit.input_related
        mutable_unit.output_related = unit.output_related
        return mutable_unit

    @classmethod
    def init_from_channel_analyzer(cls, model, analyzer=None):
        """Init MutableChannelUnits from a ChannelAnalyzer."""

        if analyzer is None:
            from mmrazor.models.task_modules.tracer import ChannelAnalyzer
            analyzer = ChannelAnalyzer()
        if isinstance(analyzer, dict):
            analyzer = TASK_UTILS.build(analyzer)
        unit_config = analyzer.analyze(model)
        return [cls.init_from_cfg(model, cfg) for cfg in unit_config.values()]

    # tools

    @property
    def name(self) -> str:
        """str: name of the unit"""
        if len(self.output_related) + len(self.input_related) > 0:
            first_module = (list(self.output_related) +
                            list(self.input_related))[0]
            first_module_name = f'{first_module.name}_{first_module.index}'
        else:
            first_module_name = 'unitx'
        name = f'{first_module_name}_{self.num_channels}'
        return getattr(self, '_name', name)

    @name.setter
    def name(self, unit_name) -> None:
        self._name = unit_name

    @property
    def alias(self) -> str:
        """str: alias of the unit"""
        return self.name

    def config_template(self,
                        with_init_args=False,
                        with_channels=False) -> Dict:
        """Generate a config template which can be used to initialize a
        ChannelUnit by cls.init_from_cfg(**kwargs)"""
        config = {}
        if with_init_args:
            config['init_args'] = {'num_channels': self.num_channels}
        if with_channels:
            config['channels'] = self._channel_dict()
        return config

    # node operations

    def add_output_related(self, channel: Channel):
        """Add a Channel which is output related."""
        assert channel.is_output_channel
        if channel not in self.output_related:
            self.output_related.append(channel)

    def add_input_related(self, channel: Channel):
        """Add a Channel which is input related."""
        assert channel.is_output_channel is False
        if channel not in self.input_related:
            self.input_related.append(channel)

    # others

    def extra_repr(self) -> str:
        s = super().extra_repr()
        s += f'name={self.name}'
        return s

    # private methods

    def _channel_dict(self) -> Dict:
        """Return channel config."""
        return {
            'input_related': [
                channel.config_template() for channel in self.input_related
            ],
            'output_related': [
                channel.config_template() for channel in self.output_related
            ],
        }
