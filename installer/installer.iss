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
AppVerName={#MyAppName} {#MyAppVersion}
AppPublisher={#MyAppPublisher}
AppPublisherURL={#MyAppURL}
AppSupportURL={#MyAppURL}/issues
; 安装包自身属性（资源管理器「属性」与缓存键）
; 注意：公司字段官方关键字是 VersionInfoCompany（不是 VersionInfoCompanyName）
VersionInfoVersion={#MyAppVersion}
VersionInfoProductVersion={#MyAppVersion}
VersionInfoProductName={#MyAppName}
VersionInfoCompany={#MyAppPublisher}
VersionInfoDescription={#MyAppName} 安装程序
VersionInfoCopyright=Copyright (C) {#MyAppPublisher}
; 用户级安装（同 Chrome/VS Code）：装到当前用户目录，安装与更新全程无 UAC
PrivilegesRequired=lowest
DefaultDirName={autopf}\{#MyAppName}
DefaultGroupName={#MyAppName}
DisableProgramGroupPage=yes
; 允许用户选择目录（安装向导第二页）
DisableDirPage=no
OutputDir=..\dist
OutputBaseFilename=公众号助手-安装包-v{#MyAppVersion}
SetupIconFile=..\assets\app.ico
; 控制面板「程序和功能」与卸载列表图标：直接用主程序 exe（与桌面快捷方式一致）
UninstallDisplayIcon={app}\{#MyAppExeName}
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
; 安装时自动关闭正在运行的程序（内置更新依赖此项接管旧进程）
CloseApplications=yes
RestartApplications=no

[Languages]
Name: "chinesesimplified"; MessagesFile: "ChineseSimplified.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"

[Files]
Source: "..\dist\{#MyAppExeName}"; DestDir: "{app}"; Flags: ignoreversion

[UninstallDelete]
; 卸载时清运行期缓存；用户数据（.env / runs）保留
Type: filesandordirs; Name: "{app}\webview_data"
Type: filesandordirs; Name: "{app}\update"
Type: files; Name: "{app}\app.log"
Type: files; Name: "{app}\app.log.1"
Type: files; Name: "{app}\ui_state.json"

[Icons]
; 所有快捷方式强制 IconFilename 指向主程序，避免沿用旧壳缓存/卸载器默认图
Name: "{group}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; IconFilename: "{app}\{#MyAppExeName}"; IconIndex: 0
Name: "{group}\卸载 {#MyAppName}"; Filename: "{uninstallexe}"; IconFilename: "{app}\{#MyAppExeName}"; IconIndex: 0
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; IconFilename: "{app}\{#MyAppExeName}"; IconIndex: 0; Tasks: desktopicon

[Run]
; skipifsilent：静默更新由 run_update.bat 明确启动新版，避免双开
Filename: "{app}\{#MyAppExeName}"; Description: "{cm:LaunchProgram,{#MyAppName}}"; Flags: nowait postinstall skipifsilent

[Code]
const
  SHCNE_ASSOCCHANGED = $08000000;
  SHCNF_IDLIST = $0000;

procedure SHChangeNotify(wEventId, uFlags: Integer; dwItem1, dwItem2: Integer);
  external 'SHChangeNotify@shell32.dll stdcall';

procedure CurStepChanged(CurStep: TSetupStep);
begin
  { 安装结束后通知 Shell 刷新图标/关联，减轻「覆盖安装后仍显示旧图标」 }
  if CurStep = ssPostInstall then
    SHChangeNotify(SHCNE_ASSOCCHANGED, SHCNF_IDLIST, 0, 0);
end;
