#pragma once
#include <cstdint>
#include <cstring>

inline void DxilMd5Block(uint32_t state[4], const uint32_t x[16]) {
    static const uint32_t k[64] = {
        0xd76aa478, 0xe8c7b756, 0x242070db, 0xc1bdceee, 0xf57c0faf, 0x4787c62a, 0xa8304613, 0xfd469501,
        0x698098d8, 0x8b44f7af, 0xffff5bb1, 0x895cd7be, 0x6b901122, 0xfd987193, 0xa679438e, 0x49b40821,
        0xf61e2562, 0xc040b340, 0x265e5a51, 0xe9b6c7aa, 0xd62f105d, 0x02441453, 0xd8a1e681, 0xe7d3fbc8,
        0x21e1cde6, 0xc33707d6, 0xf4d50d87, 0x455a14ed, 0xa9e3e905, 0xfcefa3f8, 0x676f02d9, 0x8d2a4c8a,
        0xfffa3942, 0x8771f681, 0x6d9d6122, 0xfde5380c, 0xa4beea44, 0x4bdecfa9, 0xf6bb4b60, 0xbebfbc70,
        0x289b7ec6, 0xeaa127fa, 0xd4ef3085, 0x04881d05, 0xd9d4d039, 0xe6db99e5, 0x1fa27cf8, 0xc4ac5665,
        0xf4292244, 0x432aff97, 0xab9423a7, 0xfc93a039, 0x655b59c3, 0x8f0ccc92, 0xffeff47d, 0x85845dd1,
        0x6fa87e4f, 0xfe2ce6e0, 0xa3014314, 0x4e0811a1, 0xf7537e82, 0xbd3af235, 0x2ad7d2bb, 0xeb86d391};
    static const uint8_t s[16] = {7, 12, 17, 22, 5, 9, 14, 20, 4, 11, 16, 23, 6, 10, 15, 21};
    uint32_t a = state[0], b = state[1], c = state[2], d = state[3];
    for (int i = 0; i < 64; i++) {
        uint32_t f;
        int g, r = i >> 4;
        if (r == 0) { f = (b & c) | (~b & d); g = i; }
        else if (r == 1) { f = (b & d) | (c & ~d); g = (5 * i + 1) & 15; }
        else if (r == 2) { f = b ^ c ^ d; g = (3 * i + 5) & 15; }
        else { f = c ^ (b | ~d); g = (7 * i) & 15; }
        uint32_t t = a + f + x[g] + k[i];
        uint8_t sh = s[r * 4 + (i & 3)];
        a = d; d = c; c = b;
        b += (t << sh) | (t >> (32 - sh));
    }
    state[0] += a; state[1] += b; state[2] += c; state[3] += d;
}

inline void DxilHashRetail(const uint8_t* data, uint32_t n, uint8_t out[16]) {
    uint32_t state[4] = {0x67452301, 0xefcdab89, 0x98badcfe, 0x10325476};
    uint32_t x[16];
    uint32_t full = n & ~63u, r = n & 63u;
    for (uint32_t off = 0; off < full; off += 64) { memcpy(x, data + off, 64); DxilMd5Block(state, x); }
    uint8_t* bx = (uint8_t*)x;
    memset(x, 0, sizeof(x));
    if (r < 56) {
        x[0] = n << 3;
        memcpy(bx + 4, data + full, r);
        bx[4 + r] = 0x80;
    } else {
        memcpy(bx, data + full, r);
        bx[r] = 0x80;
        DxilMd5Block(state, x);
        memset(x, 0, sizeof(x));
        x[0] = n << 3;
    }
    x[15] = 1 | (n << 1);
    DxilMd5Block(state, x);
    memcpy(out, state, 16);
}

inline void DxilHashContainer(uint8_t* container, uint32_t size) {
    DxilHashRetail(container + 20, size - 20, container + 4);
}
