; 公众号助手 Windows 安装包（Inno Setup 6）
; 本地编译：installer\build_installer.bat
; CI 编译：.github/workflows/build.yml（ISCC installer\installer.iss）
;
; 行为：
;   - 中文向导：欢迎页 → 选择安装目录 → 是否创建桌面快捷方式 → 安装 → 完成页可勾选立即启动
;   - 默认装到 {autopf}\公众号助手（Program Files，需管理员）；用户可改任意目录
;   - 程序数据（.env / runs / app.log）由程序自身处理：
;     安装目录可写则就地生成；Program Files 不可写时自动落 %APPDATA%\公众号助手
;   - 卸载时保留用户数据（.env 有密钥、runs 有文章产出，误删损失大）

#define MyAppName "公众号助手"
#define MyAppVersion GetEnv("APP_VERSION") == "" ? "1.0.0" : GetEnv("APP_VERSION")
#define MyAppPublisher "qq353167950"
#define MyAppURL "https://github.com/qq353167950/weixin_chat"
#define MyAppExeName "公众号助手.exe"

[Setup]
AppId={{8F1E7C3A-52B4-4E9D-9C6E-3A7B21D5E4F0}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
AppPublisherURL={#MyAppURL}
AppSupportURL={#MyAppURL}/issues
DefaultDirName={autopf}\{#MyAppName}
DefaultGroupName={#MyAppName}
DisableProgramGroupPage=yes
; 允许用户选择目录（安装向导第二页）
DisableDirPage=no
OutputDir=..\dist
OutputBaseFilename=公众号助手-安装包-v{#MyAppVersion}
SetupIconFile=..\assets\app.ico
UninstallDisplayIcon={app}\{#MyAppExeName}
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
; 安装时自动关闭正在运行的程序（内置更新依赖此项接管旧进程）
CloseApplications=yes
RestartApplications=no
; 非管理员也可安装（选不了 Program Files 时装用户目录）
PrivilegesRequiredOverridesAllowed=dialog

[Languages]
Name: "chinesesimplified"; MessagesFile: "compiler:Languages\ChineseSimplified.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"

[Files]
Source: "..\dist\{#MyAppExeName}"; DestDir: "{app}"; Flags: ignoreversion

[Icons]
Name: "{group}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"
Name: "{group}\卸载 {#MyAppName}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; Tasks: desktopicon

[Run]
; skipifsilent：静默更新由 run_update.bat 明确启动新版，避免双开
Filename: "{app}\{#MyAppExeName}"; Description: "{cm:LaunchProgram,{#MyAppName}}"; Flags: nowait postinstall skipifsilent
