# Baut die LocalBib Windows-Exe (onedir) und — wenn Inno Setup installiert ist —
# das Installationsprogramm, mit dem die Release-Pipeline (#146) ausliefert.
#
# Voraussetzung:  py -m pip install -r requirements-build.txt
#                 optional: Inno Setup 6 (https://jrsoftware.org/isdl.php)
# Aufruf:         .\build_exe.ps1                 # Version aus dem letzten git-Tag
#                 .\build_exe.ps1 -Version 0.3.0  # Version explizit setzen
#
# Ergebnis:       dist\LocalBib\LocalBib.exe                 (Anwendung, onedir)
#                 dist\installer\LocalBib-Setup-<version>.exe (Installer)
#
# Dies ist der lokale Zwilling von .github/workflows/release.yml: gleiche Spec,
# gleiches .iss, gleicher Asset-Name. Wer den Installer vor einem Release
# ausprobieren will (installieren, starten, deinstallieren), baut ihn hier.
#
# ADR-0015: Das Installationsprogramm wird offen ueber GitHub Releases des
# oeffentlichen Repos verteilt — das alte "Exe niemals auf GitHub Releases"
# aus ADR-0001 ist zurueckgezogen. Ausgeliefert wird aber nur, was
# scripts/publish_public.py exportiert hat; ein lokal gebauter Installer ist
# zum Ausprobieren da, nicht zum Verteilen.

param(
    [string]$Version
)

# Hinweis: NICHT "Stop" verwenden — PyInstaller schreibt INFO-Logs auf stderr,
# was unter PowerShell sonst als Fehler gewertet wird. Stattdessen prüfen wir
# $LASTEXITCODE explizit nach dem Build.
$ErrorActionPreference = "Continue"
Set-Location -Path $PSScriptRoot

# --- Version bestimmen (Parameter > git-Tag > 0.1.0), in VERSION-Datei schreiben ---
# Dieselbe Mechanik wie in der CI: die VERSION-Datei ist der einzige Weg, auf dem
# eine Versionsnummer in das eingefrorene Build gelangt (localbib.spec bundelt
# sie, routers/version.py liest sie beim Start).
if (-not $Version) {
    $Version = "0.1.0"
    try {
        $tag = (git describe --tags --abbrev=0 2>$null)
        if ($LASTEXITCODE -eq 0 -and $tag) { $Version = $tag.TrimStart("v") }
    } catch {}
}
$Version = $Version.TrimStart("v")
# BOM-frei schreiben: "-Encoding utf8" bedeutet in Windows PowerShell 5.1 "mit
# BOM", in pwsh 7 (und damit in der CI) "ohne". Diese Zeile muss auf beiden
# Wegen dasselbe Byte-fuer-Byte-Ergebnis liefern, sonst baut der lokale Zwilling
# eine andere Versionsnummer als das Release.
[System.IO.File]::WriteAllText(
    (Join-Path $PSScriptRoot "VERSION"), $Version, [System.Text.UTF8Encoding]::new($false))
Write-Host "Building LocalBib $Version ..." -ForegroundColor Cyan

# --- Alte Build-Artefakte entfernen (OneDrive sperrt Dateien teils kurzzeitig) ---
foreach ($d in "build", "dist") {
    if (Test-Path $d) {
        foreach ($attempt in 1..3) {
            try { Remove-Item $d -Recurse -Force -ErrorAction Stop; break }
            catch { if ($attempt -eq 3) { throw "Kann '$d' nicht löschen (gesperrt?): $_" } ; Start-Sleep 3 }
        }
    }
}

# --- PyInstaller (onedir, exakt die Spec, die auch die CI baut) ---
py -m PyInstaller localbib.spec --noconfirm
if ($LASTEXITCODE -ne 0) { throw "PyInstaller-Build fehlgeschlagen." }
if (-not (Test-Path "dist\LocalBib\LocalBib.exe")) { throw "dist\LocalBib\LocalBib.exe fehlt." }

# --- Inno Setup (optional: nur wenn ISCC.exe vorhanden ist) ---
# An mehreren Orten suchen: winget installiert Inno Setup standardmaessig pro
# Benutzer nach %LOCALAPPDATA%\Programs, das Installationsprogramm von der
# Webseite dagegen maschinenweit nach "Program Files (x86)" — und der CI-Runner
# bringt genau diese zweite Variante mit. Wer nur an einem Ort nachsieht,
# ueberspringt den Installer-Schritt stillschweigend und merkt erst am Release,
# dass er ihn lokal nie ausprobiert hat.
$isccCandidates = @(
    (Join-Path ${env:ProgramFiles(x86)} "Inno Setup 6\ISCC.exe"),
    (Join-Path $env:ProgramFiles "Inno Setup 6\ISCC.exe"),
    (Join-Path $env:LOCALAPPDATA "Programs\Inno Setup 6\ISCC.exe")
)
$iscc = $isccCandidates | Where-Object { Test-Path $_ } | Select-Object -First 1
if (-not $iscc) {
    $isccOnPath = Get-Command "ISCC.exe" -ErrorAction SilentlyContinue
    if ($isccOnPath) { $iscc = $isccOnPath.Source }
}
if ($iscc) {
    & $iscc "/DMyAppVersion=$Version" "installer\localbib.iss"
    if ($LASTEXITCODE -ne 0) { throw "Inno Setup fehlgeschlagen (Exit $LASTEXITCODE)." }
    Write-Host ""
    Write-Host "Fertig:" -ForegroundColor Green
    Write-Host "  App:        dist\LocalBib\LocalBib.exe"
    Write-Host "  Installer:  dist\installer\LocalBib-Setup-$Version.exe"
} else {
    Write-Host ""
    Write-Host "Fertig (ohne Installer):" -ForegroundColor Green
    Write-Host "  App:  dist\LocalBib\LocalBib.exe"
    Write-Host "  Inno Setup 6 nicht gefunden — Installer uebersprungen." -ForegroundColor Yellow
    Write-Host "  Gesucht in: $($isccCandidates -join ', ') sowie im PATH." -ForegroundColor Yellow
}
