<#
.SYNOPSIS
    UWF Manager Pro - 一键发布到 GitHub
.DESCRIPTION
    需要 GitHub Personal Access Token (classic, 含 repo 权限)。
    运行: .\publish.ps1 -Token "ghp_xxx" -RepoName "uwf-manager-pro" [-Username "yourname"]
#>
param(
    [Parameter(Mandatory=$true)]
    [string]$Token,
    [string]$Username = $env:USERNAME,
    [string]$RepoName = "uwf-manager-pro",
    [string]$Description = "Lightweight open-source UWF (Unified Write Filter) manager for Windows"
)

$ErrorActionPreference = "Stop"
$api = "https://api.github.com"
$auth = @{Authorization = "token $Token"}

# 1. 创建仓库
Write-Host "==> 创建 GitHub 仓库 $Username/$RepoName" -ForegroundColor Cyan
$body = @{name=$RepoName; description=$Description; `private`=$false; auto_init=$false} | ConvertTo-Json
try {
    Invoke-RestMethod -Uri "$api/user/repos" -Method Post -Headers $auth `
        -Body $body -ContentType "application/json" | Out-Null
    Write-Host "    仓库已创建" -ForegroundColor Green
} catch {
    if ($_.Exception.Response.StatusCode -eq 422) {
        Write-Host "    仓库已存在，继续" -ForegroundColor Yellow
    } else { throw }
}

# 2. 本地 git 配置并推送
Set-Location $PSScriptRoot
git remote remove origin 2>$null
git remote add origin "https://$Username`:$Token@github.com/$Username/$RepoName.git"
git branch -M main
git push -u origin main 2>&1 | Out-String | Write-Host

# 3. 创建 Release 并上传 exe
Write-Host "==> 创建 Release v1.0" -ForegroundColor Cyan
$relBody = @{tag_name="v1.0"; name="UWF Manager Pro v1.0"; body=$(
    "首个版本。`n`n功能：`n- UWF 状态面板`n- 覆盖层内存监控（阈值变色）`n- "
    + "受保护卷列表`n- 文件浏览器（定位覆盖内存占用来源）`n- 启用/禁用/提交删除`n`n"
    + "需以管理员身份运行。"
)} | ConvertTo-Json
$rel = Invoke-RestMethod -Uri "$api/repos/$Username/$RepoName/releases" `
    -Method Post -Headers $auth -Body $relBody -ContentType "application/json"

$exe = "dist/UWF Manager Pro.exe"
if (Test-Path $exe) {
    Write-Host "==> 上传 exe asset" -ForegroundColor Cyan
    $fn = [System.IO.Path]::GetFileName($exe)
    $bytes = [System.IO.File]::ReadAllBytes($exe)
    Invoke-RestMethod -Uri "$($rel.upload_url.Replace('{?name,label}',''))?name=$fn" `
        -Method Post -Headers $auth -Body $bytes `
        -ContentType "application/octet-stream" | Out-Null
    Write-Host "    上传完成" -ForegroundColor Green
}

Write-Host "`n✅ 发布成功: https://github.com/$Username/$RepoName/releases/tag/v1.0" `
    -ForegroundColor Green
