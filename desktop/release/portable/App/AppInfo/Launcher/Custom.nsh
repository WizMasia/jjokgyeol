${SegmentFile}

${Segment.onInit}
    InitPluginsDir
    ClearErrors
    File /oname=$PLUGINSDIR\prepare-webview.ps1 "${PACKAGE}\Other\Source\prepare-webview.ps1"
    ${If} ${Errors}
        SetErrorLevel 1
        Abort
    ${EndIf}
    nsExec::ExecToStack '"$SYSDIR\WindowsPowerShell\v1.0\powershell.exe" -NoProfile -NonInteractive -ExecutionPolicy RemoteSigned -File "$PLUGINSDIR\prepare-webview.ps1" -Root "$EXEDIR"'
    Pop $0
    Pop $1
    ${If} $0 != 0
        MessageBox MB_ICONSTOP "Jjokgyeol could not prepare WebView2.$\r$\n$1"
        SetErrorLevel 1
        Abort
    ${EndIf}
!macroend
