!macro JjokgyeolPrepareWebView OPTIONS FAILURE
    InitPluginsDir
    ClearErrors
    File /oname=$PLUGINSDIR\prepare-webview.ps1 "prepare-webview.ps1"
    StrCpy $0 1
    ${IfNot} ${Errors}
        nsExec::ExecToLog '"$SYSDIR\WindowsPowerShell\v1.0\powershell.exe" -NoProfile -NonInteractive -ExecutionPolicy RemoteSigned -File "$PLUGINSDIR\prepare-webview.ps1" -Root "$INSTDIR" ${OPTIONS}'
        Pop $0
    ${Else}
        DetailPrint "Could not extract the embedded WebView2 preparation helper."
    ${EndIf}
    ${If} $0 != 0
        DetailPrint "${FAILURE}"
        MessageBox MB_ICONSTOP "${FAILURE}" /SD IDOK
        SetErrorLevel 1
        Abort
    ${EndIf}
!macroend

!macro CustomCodePreInstall
    !insertmacro JjokgyeolPrepareWebView "-ValidateInstall" "Installation stopped before replacing the app files. See installer details and use a writable local NTFS folder with no linked app or settings paths."
!macroend

!macro CustomCodePostInstall
    !insertmacro JjokgyeolPrepareWebView "-FinishInstall" "The app files may have been replaced. Keep the original folder and Data. To roll back, install the previous working PAF into a fresh local NTFS folder and restore your pre-update Data backup. See installer details."
!macroend
