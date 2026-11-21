# Contributing

## Xbox fork: what must never be committed

This applies to every branch, pull request, issue, comment and attachment. Pull requests target `xbox-series`.

Never add, attach or paste:

- Game files: executables, DLLs, packages, archives, assets, textures, audio, saves, memory dumps, or any part of a console image.
- Shader data taken from a game: binaries, DXIL or DXBC, LLVM IR, SPIR-V, ISA or disassembly listings, or tables extracted from them. A shader a test needs is built inside the test from a few synthetic instructions.
- Keys, certificates, licences, tokens, console identifiers or user data, and any decrypted or unpacked content from an encrypted package. Do not add tooling or instructions to decrypt, unpack or bypass protection of content you do not already have the right to read.
- Microsoft SDK or GDK files, headers, import libraries, or leaked or unreleased material. Interfaces are implemented from public documentation and from the licensed sources listed in `tools/xbox/platform/README.md`.
- Binary files of any kind, and any file over 500 KB. Small result files (counts and pass or fail per shader index, in JSON) are fine.

Describe, do not copy. Writing down what a format, layout or behaviour is, in your own words and with synthetic examples, is the point of `docs/xbox`. Pasting a listing, dump or table from a game is not.

Run your corpus and game data on your own machine and keep it there. Share outcomes (counts, error classes, a one-line symptom), not inputs. Strip game and file names, paths and user names from logs before posting them.

Stage files by name, never a whole directory, and check `git diff --cached --stat` before every commit. If something that breaks these rules reaches a branch, a pull request or an issue, tell a maintainer straight away; deleting it in a later commit does not remove it from history. If the repository is ever taken down, the maintainer will not put it back online.
