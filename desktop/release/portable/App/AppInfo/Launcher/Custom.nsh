${SegmentFile}

${Segment.onInit}
    nsExec::ExecToLog '"$SYSDIR\WindowsPowerShell\v1.0\powershell.exe" -NoProfile -NonInteractive -ExecutionPolicy RemoteSigned -File "$EXEDIR\App\Jjokgyeol\prepare-webview.ps1"'
    Pop $0
    ${If} $0 != 0
        MessageBox MB_ICONSTOP "Jjokgyeol could not prepare WebView2. Use a writable local NTFS folder on Windows x64."
        SetErrorLevel 1
        Abort
    ${EndIf}
!macroend
