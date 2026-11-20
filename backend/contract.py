"""Small wire format; expand locally without asking the model to write a report."""
import json
import re

from .taxonomy import TYPES

VERSION = 2
ENUMS = {
    'p': ['present', 'absent', 'unknown'],
    'a': ['well_separated', 'touching', 'bundled', 'mixed', 'not_applicable', 'unknown'],
    'k': ['inside', 'on_or_near', 'absent', 'unknown'],
    'v': ['clear', 'partly_obscured', 'obscured', 'not_applicable', 'unknown'],
    's': ['present', 'absent', 'unknown'],
    'l': ['on_pan', 'in_scale_enclosure', 'near_scale', 'not_on_scale', 'unknown'],
    'd': ['readable', 'unreadable', 'not_visible', 'not_applicable', 'unknown'],
    'h': ['present', 'absent', 'unknown'],
    'b': ['present', 'absent', 'unknown'],
    'g': ['present', 'absent', 'unknown'],
}
PROPERTIES = {key: {'type': 'string', 'enum': values}
              for key, values in ENUMS.items()}
PROPERTIES.update({
    't': {'type': 'array', 'items': {'type': 'string', 'enum': sorted(TYPES)}, 'maxItems': 10},
    'c': {'type': ['integer', 'null'], 'minimum': 0},
    'w': {'type': ['string', 'null'], 'maxLength': 32},
    'n': {'type': 'string', 'minLength': 1, 'maxLength': 240},
})
SCHEMA = {'type': 'object', 'properties': PROPERTIES,
          'required': list(PROPERTIES), 'additionalProperties': False}
WIRE_KEYS = dict(p='presence', t='types', a='arrangement', c='count', k='packet',
                 v='packet_visibility', s='scale', l='scale_position', d='display',
                 w='weight', h='threads', b='black_beads', g='coloured_stones', n='note')


