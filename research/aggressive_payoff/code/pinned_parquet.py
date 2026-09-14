"""Small fail-closed reader for the flat pinned Release's Parquet encoding.

This is NOT a general Parquet implementation. It supports only the physical
primitive types/page encodings explicitly used by this fixed SHA-verified ZIP.
No downloads, optional engines, or synthesized market records are involved.
"""
from __future__ import annotations
import ctypes
import ctypes.util
import gzip
import struct
from pathlib import Path


class Compact:
    def __init__(self, data, position=0):
        self.data = data
        self.position = position

    def take(self, n):
        if n < 0 or self.position + n > len(self.data):
            raise ValueError('truncated parquet compact stream')
        result = self.data[self.position:self.position+n]
        self.position += n
        return result

    def byte(self):
        return self.take(1)[0]

    def uint(self):
        value, shift = 0, 0
        while True:
            b = self.byte()
            value |= (b & 127) << shift
            if b < 128:
                return value
            shift += 7
            if shift > 63:
                raise ValueError('invalid compact varint')

    def sint(self):
        u = self.uint()
        return (u >> 1) ^ -(u & 1)

    def value(self, kind):
        if kind == 1: return True
        if kind == 2: return False
        if kind == 3: return struct.unpack('b', self.take(1))[0]
        if kind in (4, 5, 6): return self.sint()
        if kind == 7: return struct.unpack('<d', self.take(8))[0]
        if kind == 8: return self.take(self.uint())
        if kind in (9, 10):
            tag = self.byte()
            length, subtype = tag >> 4, tag & 15
            if length == 15: length = self.uint()
            return [self.value(self.byte()) if subtype in (1, 2) else self.value(subtype) for _ in range(length)]
        if kind == 11:
            n = self.uint()
            if not n: return {}
            tag = self.byte()
            return {self.value(tag >> 4): self.value(tag & 15) for _ in range(n)}
        if kind == 12: return self.record()
        raise ValueError(f'unsupported compact type {kind}')

    def record(self):
        fields, previous = {}, 0
        while True:
            tag = self.byte()
            if tag == 0: return fields
            delta, kind = tag >> 4, tag & 15
            number = previous + delta if delta else self.sint()
            previous = number
            fields[number] = self.value(kind)


def metadata(data):
    if data[:4] != b'PAR1' or data[-4:] != b'PAR1':
        raise ValueError('invalid parquet magic')
    size = struct.unpack('<I', data[-8:-4])[0]
    reader = Compact(data, len(data)-8-size)
    result = reader.record()
    if reader.position != len(data)-8:
        raise ValueError('unexpected footer bytes')
    return result


def decompress(data, codec, expected):
    if codec == 0:
        result = data
    elif codec == 1:
        # Use the already installed system libsnappy; do not install anything.
        library = ctypes.util.find_library('snappy')
        if not library:
            raise RuntimeError('local libsnappy unavailable; no network fallback')
        lib = ctypes.CDLL(library)
        lib.snappy_uncompress.argtypes = [ctypes.c_char_p, ctypes.c_size_t, ctypes.c_void_p, ctypes.POINTER(ctypes.c_size_t)]
        lib.snappy_uncompress.restype = ctypes.c_int
        output = ctypes.create_string_buffer(expected)
        size = ctypes.c_size_t(expected)
        if lib.snappy_uncompress(data, len(data), output, ctypes.byref(size)) != 0:
            raise ValueError('snappy decode error')
        result = output.raw[:size.value]
    else:
        raise ValueError(f'unsupported pinned parquet codec {codec}')
    if len(result) != expected:
        raise ValueError('decompressed page size differs')
    return result


def plain(data, physical, count):
    if physical == 0:
        if len(data) < (count+7)//8: raise ValueError('truncated boolean page')
        return [bool((data[i//8] >> (i%8)) & 1) for i in range(count)]
    if physical in (1, 2, 4, 5):
        fmt, size = {1: ('i', 4), 2: ('q', 8), 4: ('f', 4), 5: ('d', 8)}[physical]
        if len(data) != count * size: raise ValueError('plain numeric byte count differs')
        return list(struct.unpack('<' + str(count) + fmt, data))
    if physical == 6:
        reader = Compact(data)
        values = []
        for _ in range(count):
            size = struct.unpack('<I', reader.take(4))[0]
            values.append(reader.take(size).decode('utf-8'))
        if reader.position != len(data): raise ValueError('extra string bytes')
        return values
    raise ValueError(f'unsupported pinned physical type {physical}')


def hybrid(data, width, count):
    if not 0 <= width <= 32: raise ValueError('unsupported RLE bit width')
    reader, result = Compact(data), []
    while len(result) < count:
        head = reader.uint()
        if head == 0: raise ValueError('empty RLE run')
        if head & 1:
            run = (head >> 1) * 8
            size = run * width // 8
            raw = reader.take(size)
            mask = (1 << width) - 1
            for i in range(run):
                offset = i * width
                start, shift = offset // 8, offset % 8
                value = int.from_bytes(raw[start:(offset + width + 7)//8], 'little')
                result.append((value >> shift) & mask)
        else:
            run = head >> 1
            value = int.from_bytes(reader.take((width+7)//8), 'little')
            result.extend([value] * run)
        if len(result) > count + 7: raise ValueError('unexpected RLE padding')
    if reader.position != len(data): raise ValueError('unused RLE bytes')
    return result[:count]


def read_parquet(path: Path):
    import pandas as pd
    data = path.read_bytes()
    meta = metadata(data)
    schema = meta[2]
    if schema[0].get(5) != len(schema)-1 or any(s.get(5, 0) for s in schema[1:]):
        raise ValueError('only flat pinned schema is supported')
    fields = {s[4].decode(): s for s in schema[1:]}
    output = {key: [] for key in fields}
    for group in meta[4]:
        for chunk in group[1]:
            cm = chunk[3]
            if len(cm[3]) != 1: raise ValueError('nested column path unsupported')
            name = cm[3][0].decode()
            definition_max = fields[name][3]
            if definition_max not in (0, 1): raise ValueError('repeated field unsupported')
            position = min(cm[9], cm.get(11, cm[9]))
            end = position + cm[7]
            values, dictionary = [], None
            while position < end:
                reader = Compact(data, position)
                header = reader.record()
                position = reader.position
                raw = data[position:position+header[3]]
                position += header[3]
                page = decompress(raw, cm[4], header[2])
                if header[1] == 2:
                    if dictionary is not None or header[7][2] != 0:
                        raise ValueError('unsupported dictionary page')
                    dictionary = plain(page, cm[1], header[7][1])
                    continue
                if header[1] != 0:
                    raise ValueError('only V1 data pages supported by pinned reader')
                ph = header[5]
                count, encoding = ph[1], ph[2]
                if definition_max:
                    if ph[3] != 3: raise ValueError('unsupported definition encoding')
                    size = struct.unpack('<I', page[:4])[0]
                    levels = hybrid(page[4:4+size], 1, count)
                    payload = page[4+size:]
                else:
                    levels, payload = [1] * count, page
                actual = sum(levels)
                if encoding == 0:
                    decoded = plain(payload, cm[1], actual)
                elif encoding in (2, 8):
                    if dictionary is None: raise ValueError('missing dictionary')
                    indices = hybrid(payload[1:], payload[0], actual) if actual else []
                    if any(i >= len(dictionary) for i in indices): raise ValueError('dictionary index outside range')
                    decoded = [dictionary[i] for i in indices]
                else:
                    raise ValueError(f'unsupported encoding {encoding}')
                it = iter(decoded)
                values.extend(next(it) if valid else None for valid in levels)
            if position != end or len(values) != cm[5] or len(values) != group[3]:
                raise ValueError('column chunk length/row count mismatch')
            output[name].extend(values)
    if any(len(v) != meta[3] for v in output.values()):
        raise ValueError('file row count mismatch')
    return pd.DataFrame(output)
