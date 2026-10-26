; Tauri NSIS installer hooks for the user-facing shortcut name only.
; Technical product, binary, bundle, install and update identities stay unchanged.

!define MOADOOM_SHORTCUT_NAME "모아둠"
!define MOADOOM_LEGACY_SHORTCUT_NAME "Insurance AI"
!define MOADOOM_MAIN_BINARY_NAME "Insurance AI"
!define MOADOOM_BRAND_ICON_FILE "moadoom-brand-b.ico"

Var MoadoomInstallCompleted

; Rename an existing legacy shortcut only when its target is exactly this app.
; If the destination exists for another target, preserve both links unchanged.
; If both names target this app, remove only the legacy duplicate.
!macro MoadoomRenameOwnedShortcut OLD_PATH NEW_PATH
  !define MoadoomRenameUnique ${__LINE__}
  !insertmacro IsShortcutTarget "${OLD_PATH}" "$INSTDIR\${MOADOOM_MAIN_BINARY_NAME}.exe"
  Pop $R8
  ${If} $R8 = 1
    IfFileExists "${NEW_PATH}" moadoom_existing_${MoadoomRenameUnique} moadoom_rename_${MoadoomRenameUnique}

    moadoom_existing_${MoadoomRenameUnique}:
      !insertmacro IsShortcutTarget "${NEW_PATH}" "$INSTDIR\${MOADOOM_MAIN_BINARY_NAME}.exe"
      Pop $R9
      ${If} $R9 = 1
        Delete "${OLD_PATH}"
      ${EndIf}
      Goto moadoom_rename_done_${MoadoomRenameUnique}

    moadoom_rename_${MoadoomRenameUnique}:
      ClearErrors
      Rename "${OLD_PATH}" "${NEW_PATH}"

    moadoom_rename_done_${MoadoomRenameUnique}:
  ${EndIf}
  !undef MoadoomRenameUnique
!macroend

; Remove a renamed shortcut on a real uninstall only when it still targets this app.
!macro MoadoomDeleteOwnedShortcut SHORTCUT_PATH
  !insertmacro IsShortcutTarget "${SHORTCUT_PATH}" "$INSTDIR\${MOADOOM_MAIN_BINARY_NAME}.exe"
  Pop $R8
  ${If} $R8 = 1
    !insertmacro UnpinShortcut "${SHORTCUT_PATH}"
    Delete "${SHORTCUT_PATH}"
  ${EndIf}
!macroend

; Change only the icon location of an existing shortcut. Loading and saving the
; same ShellLink preserves its target, arguments, working directory and other
; properties created by Tauri.
!macro MoadoomSetShortcutIconLocation SHORTCUT_PATH
  !insertmacro ComHlpr_CreateInProcInstance ${CLSID_ShellLink} ${IID_IShellLink} r0 ""
  ${If} $0 P<> 0
    ${IUnknown::QueryInterface} $0 '("${IID_IPersistFile}",.r1)'
    ${If} $1 P<> 0
      ${IPersistFile::Load} $1 '("${SHORTCUT_PATH}", ${STGM_READWRITE})'
      ${IShellLink::SetIconLocation} $0 '("$INSTDIR\${MOADOOM_BRAND_ICON_FILE}", 0)'
      ${IPersistFile::Save} $1 '("${SHORTCUT_PATH}",1)'
      ${IUnknown::Release} $1 ""
    ${EndIf}
    ${IUnknown::Release} $0 ""
  ${EndIf}
!macroend

; Never alter a shortcut unless the bundled icon exists and the link target is
; exactly this installation's executable.
!macro MoadoomSetOwnedShortcutIcon SHORTCUT_PATH
  !define MoadoomIconUnique ${__LINE__}
  IfFileExists "$INSTDIR\${MOADOOM_BRAND_ICON_FILE}" 0 moadoom_icon_done_${MoadoomIconUnique}
  !insertmacro IsShortcutTarget "${SHORTCUT_PATH}" "$INSTDIR\${MOADOOM_MAIN_BINARY_NAME}.exe"
  Pop $R8
  ${If} $R8 = 1
    !insertmacro MoadoomSetShortcutIconLocation "${SHORTCUT_PATH}"
  ${EndIf}
  moadoom_icon_done_${MoadoomIconUnique}:
  !undef MoadoomIconUnique
!macroend

!macro MoadoomMigrateDesktopShortcut
  !insertmacro MoadoomRenameOwnedShortcut \
    "$DESKTOP\${MOADOOM_LEGACY_SHORTCUT_NAME}.lnk" \
    "$DESKTOP\${MOADOOM_SHORTCUT_NAME}.lnk"
!macroend

!macro MoadoomApplyDesktopShortcutIcon
  !insertmacro MoadoomSetOwnedShortcutIcon \
    "$DESKTOP\${MOADOOM_SHORTCUT_NAME}.lnk"
  ; A destination-name collision can intentionally leave the owned legacy link.
  !insertmacro MoadoomSetOwnedShortcutIcon \
    "$DESKTOP\${MOADOOM_LEGACY_SHORTCUT_NAME}.lnk"
!macroend

!macro MoadoomMigrateStartMenuShortcuts
  ; Current configuration uses the Start Menu root. Keep folder handling for
  ; an already-installed Tauri shortcut without creating any new shortcut.
  ${If} $AppStartMenuFolder != ""
    !insertmacro MoadoomRenameOwnedShortcut \
      "$SMPROGRAMS\$AppStartMenuFolder\${MOADOOM_LEGACY_SHORTCUT_NAME}.lnk" \
      "$SMPROGRAMS\$AppStartMenuFolder\${MOADOOM_SHORTCUT_NAME}.lnk"
  ${EndIf}
  !insertmacro MoadoomRenameOwnedShortcut \
    "$SMPROGRAMS\${MOADOOM_LEGACY_SHORTCUT_NAME}.lnk" \
    "$SMPROGRAMS\${MOADOOM_SHORTCUT_NAME}.lnk"
!macroend

!macro MoadoomApplyStartMenuShortcutIcons
  ${If} $AppStartMenuFolder != ""
    !insertmacro MoadoomSetOwnedShortcutIcon \
      "$SMPROGRAMS\$AppStartMenuFolder\${MOADOOM_SHORTCUT_NAME}.lnk"
    !insertmacro MoadoomSetOwnedShortcutIcon \
      "$SMPROGRAMS\$AppStartMenuFolder\${MOADOOM_LEGACY_SHORTCUT_NAME}.lnk"
  ${EndIf}
  !insertmacro MoadoomSetOwnedShortcutIcon \
    "$SMPROGRAMS\${MOADOOM_SHORTCUT_NAME}.lnk"
  !insertmacro MoadoomSetOwnedShortcutIcon \
    "$SMPROGRAMS\${MOADOOM_LEGACY_SHORTCUT_NAME}.lnk"
!macroend

!macro NSIS_HOOK_POSTINSTALL
  ; Tauri has already honored /NS and its normal shortcut policy here.
  ; These operations only migrate shortcuts that actually exist and are owned.
  StrCpy $MoadoomInstallCompleted 1
  !insertmacro MoadoomMigrateStartMenuShortcuts
  !insertmacro MoadoomMigrateDesktopShortcut
  !insertmacro MoadoomApplyStartMenuShortcutIcons
  !insertmacro MoadoomApplyDesktopShortcutIcon
!macroend

!macro NSIS_HOOK_PREUNINSTALL
  ; During an updater-driven uninstall, keep the new shortcuts for the next install.
  ${If} $UpdateMode != 1
    ${If} $AppStartMenuFolder != ""
      !insertmacro MoadoomDeleteOwnedShortcut \
        "$SMPROGRAMS\$AppStartMenuFolder\${MOADOOM_SHORTCUT_NAME}.lnk"
    ${EndIf}
    !insertmacro MoadoomDeleteOwnedShortcut \
      "$SMPROGRAMS\${MOADOOM_SHORTCUT_NAME}.lnk"
    !insertmacro MoadoomDeleteOwnedShortcut \
      "$DESKTOP\${MOADOOM_SHORTCUT_NAME}.lnk"
  ${EndIf}
!macroend

; In interactive installs Tauri creates the optional desktop shortcut from the
; finish page, after NSIS_HOOK_POSTINSTALL. Migrate it only after install success.
Function .onGUIEnd
  ${If} $MoadoomInstallCompleted = 1
    !insertmacro MoadoomMigrateDesktopShortcut
    !insertmacro MoadoomApplyDesktopShortcutIcon
  ${EndIf}
FunctionEnd
