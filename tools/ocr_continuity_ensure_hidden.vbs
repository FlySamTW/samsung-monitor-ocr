Option Explicit

If WScript.Arguments.Count <> 4 Then
    WScript.Quit 64
End If

Dim repoRoot, sourceRoot, outputDir, backendUrl
repoRoot = WScript.Arguments(0)
sourceRoot = WScript.Arguments(1)
outputDir = WScript.Arguments(2)
backendUrl = WScript.Arguments(3)

Dim fso, shell, installer, command, exitCode
Set fso = CreateObject("Scripting.FileSystemObject")
Set shell = CreateObject("WScript.Shell")
installer = fso.BuildPath(fso.BuildPath(repoRoot, "tools"), "install_ocr_continuity_daemon.ps1")

If Not fso.FileExists(installer) Then
    WScript.Quit 66
End If

command = "powershell.exe -NoLogo -NoProfile -NonInteractive -WindowStyle Hidden -ExecutionPolicy Bypass -File " _
    & Quote(installer) & " -Action ensure -RepoRoot " & Quote(repoRoot) _
    & " -SourceRoot " & Quote(sourceRoot) & " -OutputDir " & Quote(outputDir) _
    & " -BackendUrl " & Quote(backendUrl)

exitCode = shell.Run(command, 0, True)
WScript.Quit exitCode

Function Quote(value)
    Quote = Chr(34) & Replace(CStr(value), Chr(34), Chr(34) & Chr(34)) & Chr(34)
End Function
