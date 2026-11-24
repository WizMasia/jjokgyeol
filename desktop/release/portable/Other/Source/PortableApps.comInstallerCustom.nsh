!macro JjokgyeolPrepareWebView OPTIONS
    InitPluginsDir
    ClearErrors
    File /oname=$PLUGINSDIR\prepare-webview.ps1 "prepare-webview.ps1"
    ${If} ${Errors}
        SetErrorLevel 1
        Abort
    ${EndIf}
    nsExec::ExecToLog '"$SYSDIR\WindowsPowerShell\v1.0\powershell.exe" -NoProfile -NonInteractive -ExecutionPolicy RemoteSigned -File "$PLUGINSDIR\prepare-webview.ps1" -Root "$INSTDIR" ${OPTIONS}'
    Pop $0
    ${If} $0 != 0
        MessageBox MB_ICONSTOP "Jjokgyeol could not prepare the installation. See installer details and use a writable local NTFS folder on Windows x64." /SD IDOK
        SetErrorLevel 1
        Abort
    ${EndIf}
!macroend

!macro CustomCodePreInstall
    !insertmacro JjokgyeolPrepareWebView "-ValidateInstall"
!macroend

!macro CustomCodePostInstall
    !insertmacro JjokgyeolPrepareWebView ""
!macroend
