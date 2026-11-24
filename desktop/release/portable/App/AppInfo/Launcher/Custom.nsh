${SegmentFile}

; Run before Core.onInit and before Settings creates or updates portable Data.
${Segment.onInit}
    ${If} $EXEFILE != "JjokgyeolPortable.exe"
        MessageBox MB_ICONSTOP "Use the installed launcher name JjokgyeolPortable.exe."
        SetErrorLevel 1
        Quit
    ${EndIf}
    InitPluginsDir
    ClearErrors
    File /oname=$PLUGINSDIR\prepare-webview.ps1 "${PACKAGE}\Other\Source\prepare-webview.ps1"
    ${If} ${Errors}
        SetErrorLevel 1
        Quit
    ${EndIf}
    nsExec::ExecToStack '"$SYSDIR\WindowsPowerShell\v1.0\powershell.exe" -NoProfile -NonInteractive -ExecutionPolicy RemoteSigned -File "$PLUGINSDIR\prepare-webview.ps1" -Root "$EXEDIR"'
    Pop $0
    Pop $1
    ${If} $0 != 0
        MessageBox MB_ICONSTOP "Jjokgyeol could not prepare its portable files.$\r$\n$1$\r$\nKeep Data. For recovery, install a working PAF in a fresh local NTFS folder and restore the matching Data backup."
        SetErrorLevel 1
        Quit
    ${EndIf}
    ReadINIStr $0 "$EXEDIR\App\AppInfo\appinfo.ini" "Details" "AppID"
    ${If} $0 != "JjokgyeolPortable"
        MessageBox MB_ICONSTOP "Invalid portable app ID. Reinstall the PAF in a fresh local NTFS folder and keep your Data backup."
        SetErrorLevel 1
        Quit
    ${EndIf}
!macroend
