# xgr: xgameruntime stand-in

`idl_table.py` + `gen_stub.py` build a typed interface table from IDL files and generate `xgameruntime.dll` source from it (not committed: the IDLs are LGPL, from the Xodus project).
Two builds, from an x64 VS prompt in this directory:

```
python -I idl_table.py <xodus>/include table.json
python -I gen_stub.py table.json gen
:: logging stub only (every method logs its first call and fails)
cl /nologo /LD /O2 /EHsc /std:c++17 gen\xgr_stub.cpp /Fe:xgameruntime.dll /link /DEF:gen\xgameruntime.def
:: with the runtime: XTaskQueue/XAsync and system info (vendored from GrandTheftXbox, MIT, runtime\GTX_LICENSE.txt) plus an offline local user (runtime\impl_user.cpp)
cl /nologo /LD /O2 /EHsc /std:c++20 /DXGR_WITH_RUNTIME /I runtime gen\xgr_stub.cpp runtime\xtask.cpp runtime\impl.cpp runtime\impl_user.cpp runtime\impl_storage.cpp runtime\impl_package.cpp runtime\gtx\gtx_log.cpp /Fe:xgameruntime.dll /link /DEF:gen\xgameruntime.def
cl /nologo /EHsc test_client.cpp
```

Environment: `XGR_LOG=<file>` (interfaces queried, first call of each stubbed method, unknown interface ids), `GTX_LOG=<file>` (the vendored layer), `XGR_GAMERTAG`, `XGR_USER_ID`, `XGR_PLS_DIR` (persistent local storage directory; default `PersistentLocalStorage` next to the executable), `XGR_PLS_SIZE_MB` (its quota; default the `SizeMB` of `MicrosoftGame.Config` next to the executable), `XGR_LOCALE` (the locale `XPackageGetUserLocale` reports; default the first preferred Windows UI language the title's `MicrosoftGame.Config` lists).
`test_client.cpp` reaches the DLL the way the GDK's static library does (`QueryApiImpl` + vtable slots): it adds the local user through the async framework, reads id, state and a privilege,
and the system analytics info (reports Windows.Xbox / Xbox Series X). Implemented: the whole XAsync/XTaskQueue layer, XThread hints, system and title ids, and the user functions with no network (XUserAdd*, ids, gamertag, state, age group, privileges, change-event registration), and persistent local storage (path, path size, space info: a directory limited to the title's declared quota), and two package queries (packaged process, user locale).
Everything else logs and fails: save games, package store, mounts and chunks, store, game UI, networking, gamer picture, tokens.
Vendored files (`runtime/xtask.*`, `xgr_internal.h`, `impl.cpp`, `gtx/`) are GrandTheftXbox's; `impl.cpp` has one local change, marked in the file.

Behaviour of the implemented methods where the console is not matched exactly (written from the interface signatures and the public GDK documentation):
* Persistent local storage (`runtime/impl_storage.cpp`): a directory, `XGR_PLS_DIR` or `PersistentLocalStorage` next to the executable, created on first use; the path ends in a backslash like the console's `T:\`.
  Quota: `XGR_PLS_SIZE_MB` or the `SizeMB` of `MicrosoftGame.Config`; a title that declares none gets `E_NOTIMPL` (the console's answer is not known).
  Space info: total = quota, used = size of the files under the directory, total free = quota - used, available = that limited by the PC volume's free space (no cluster or metadata accounting).
  `PromptUserForSpaceAsync/Result` (system UI) and `MountForPackage` log and fail.
* `XUserGetGamertag`: `XGR_GAMERTAG` or `Player`; Classic is cut to 15 bytes; the offline profile has no modern suffix, so ModernSuffix is empty and UniqueModern equals Modern.
* A buffer shorter than the text plus its terminator gets `HRESULT_FROM_WIN32(ERROR_INSUFFICIENT_BUFFER)`; the console's code for that case is not verified.
* `XPackageIsPackagedProcess` (`runtime/impl_package.cpp`): true, as for a console title. `XPackageGetUserLocale`: `XGR_LOCALE`, else the first of the user's preferred Windows UI languages that `MicrosoftGame.Config` lists under `Resources` (case-insensitive), then the first listed language with the same primary subtag, then the first listed language; with none listed, the user's default locale. The console's matching rules (script subtags, regional fallbacks) are not verified.
* `runtime/impl.cpp` (vendored) also registers `impl_storage.cpp`'s and `impl_package.cpp`'s overrides, next to `impl_user.cpp`'s.
