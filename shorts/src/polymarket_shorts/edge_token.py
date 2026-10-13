"""edge-tts가 Microsoft 음성 서버에 내는 Sec-MS-GEC v2 토큰.

2026-10-10 무렵부터 서버가 edge-tts 7.2.8(최신판)의 v1 토큰을 403으로 거절한다 — 10/12·10/13
쇼츠가 음성 합성에서 멈췄고, 롱폼은 쇼츠 뒤에 이어 만들어지므로 함께 빠졌다. 상류 이슈
rany2/edge-tts#490, 고침은 아직 머지되지 않은 PR #492다. 이 파일은 그 PR의 토큰 계산을 그대로
옮긴 것이고, `install()`이 edge-tts의 토큰 생성과 버전 표기만 바꿔 끼운다(주소·헤더·스트림은
그대로). **상류가 v2를 낸 판으로 올리면 이 파일과 `tts.py`의 `install()` 호출을 지운다.**
"""

from __future__ import annotations

import hashlib
import struct

from edge_tts import communicate
from edge_tts.constants import CHROMIUM_FULL_VERSION, TRUSTED_CLIENT_TOKEN
from edge_tts.drm import DRM, S_TO_NS, WIN_EPOCH

# 브라우저 쪽 구현이 토큰 소금 앞에 붙여 해시하는 상수다.
_PREFIX = b"Copyright (C) Microsoft Corporation. All rights reserved."
_U32 = 0xFFFFFFFF


def _rol16(value: int) -> int:
    value &= _U32
    return ((value << 16) | (value >> 16)) & _U32


def _words(data: bytes) -> list[int]:
    """리틀엔디언 32비트 낱말로 자르고, 개수가 홀수면 0을 하나 붙인다."""
    padded = data + b"\x00" * ((-len(data)) % 4)
    words = list(struct.unpack(f"<{len(padded) // 4}I", padded))
    return words + [0] * (len(words) % 2)


def _cs64_word_swap(words: list[int], key: list[int]) -> tuple[int, int]:
    a, b = key[0] | 1, key[1] | 1
    acc = last = 0
    for x, y in zip(words[0::2], words[1::2]):
        t = _rol16((_rol16(x) * 0xEF0569FB + x * a) & _U32)
        q = (t * 0x76F50F34) & _U32
        acc = (acc + ((_rol16(q) * 0xC3EFEA97 + q) & _U32)) & _U32
        t = _rol16((_rol16(y) * 0xC31713DB + y * b) & _U32)
        q = (t * 0x1881CE3C) & _U32
        last = (_rol16(q) * 0x35BD1EC9 + q) & _U32
        acc = (acc + last) & _U32
    return last, acc


def _cs64_reversible(words: list[int], key: list[int]) -> tuple[int, int]:
    a, b = key[0] | 1, key[1] | 1
    state = acc = 0
    for x, y in zip(words[0::2], words[1::2]):
        state = ((state + x) * a) & _U32
        for const in (0xCF98B111, 0x87085B9F, 0x12CEB96D, 0x257E1D83):
            state = (_rol16(state) * const) & _U32
        t = ((y + state) * b) & _U32
        t = (_rol16(t) * 0xA27416F5) & _U32
        t = _rol16((_rol16(t) * 0xD38396FF) & _U32)
        acc = (acc + state) & _U32
        state = (_rol16((t * 0x7C932B89) & _U32) * 0xBFA49F69) & _U32
        acc = (acc + state) & _U32
    return state, acc


def token(unix_timestamp: float) -> str:
    """`<CS64 16자리>-<SHA256 64자리>`. 소금은 v1과 같은 5분 단위 Windows 시각 + 고정 토큰이다."""
    ticks = unix_timestamp + WIN_EPOCH
    ticks -= ticks % 300
    ticks *= S_TO_NS / 100
    data = _PREFIX + f"{ticks:.0f}{TRUSTED_CLIENT_TOKEN}".encode("ascii")
    digest = hashlib.sha256(data).digest()
    words, key = _words(data), _words(digest)
    swap, rev = _cs64_word_swap(words, key), _cs64_reversible(words, key)
    cs64 = struct.pack("<II", swap[0] ^ rev[0], swap[1] ^ rev[1]).hex().upper()
    return cs64 + "-" + digest.hex().upper()


def install() -> None:
    # 시각은 edge-tts가 서버 Date로 보정한 값을 그대로 쓴다(403을 받으면 보정하고 한 번 다시 붙는다).
    DRM.generate_sec_ms_gec = staticmethod(lambda: token(DRM.get_unix_timestamp()))
    # 서버는 토큰 형식과 버전 앞자리(2-)가 맞는지 본다. 뒤의 브라우저 판 번호는 검사하지 않는다.
    communicate.SEC_MS_GEC_VERSION = "2-" + CHROMIUM_FULL_VERSION
