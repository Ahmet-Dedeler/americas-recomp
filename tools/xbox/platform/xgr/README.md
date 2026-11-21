# xgr: xgameruntime stand-in

`idl_table.py` + `gen_stub.py` build a typed interface table from IDL files and generate `xgameruntime.dll` source from it (not committed: the IDLs are LGPL, from the Xodus project).
Two builds, from an x64 VS prompt in this directory:

```
python -I idl_table.py <xodus>/include table.json
python -I gen_stub.py table.json gen
:: logging stub only (every method logs its first call and fails)
cl /nologo /LD /O2 /EHsc /std:c++17 gen\xgr_stub.cpp /Fe:xgameruntime.dll /link /DEF:gen\xgameruntime.def
:: with the runtime: XTaskQueue/XAsync and system info (vendored from GrandTheftXbox, MIT, runtime\GTX_LICENSE.txt) plus an offline local user (runtime\impl_user.cpp)
cl /nologo /LD /O2 /EHsc /std:c++20 /DXGR_WITH_RUNTIME /I runtime gen\xgr_stub.cpp runtime\xtask.cpp runtime\impl.cpp runtime\impl_user.cpp runtime\gtx\gtx_log.cpp /Fe:xgameruntime.dll /link /DEF:gen\xgameruntime.def
cl /nologo /EHsc test_client.cpp
```

Environment: `XGR_LOG=<file>` (interfaces queried, first call of each stubbed method, unknown interface ids), `GTX_LOG=<file>` (the vendored layer), `XGR_GAMERTAG`, `XGR_USER_ID`.
`test_client.cpp` reaches the DLL the way the GDK's static library does (`QueryApiImpl` + vtable slots): it adds the local user through the async framework, reads id, state and a privilege,
and the system analytics info (reports Windows.Xbox / Xbox Series X). Implemented: the whole XAsync/XTaskQueue layer, XThread hints, system and title ids, and the user functions with no network (XUserAdd*, ids, state, age group, privileges, change-event registration).
Everything else logs and fails: save games, packages, store, game UI, networking, gamer picture, tokens.
Vendored files (`runtime/xtask.*`, `xgr_internal.h`, `impl.cpp`, `gtx/`) are GrandTheftXbox's; `impl.cpp` has one local change, marked in the file.
