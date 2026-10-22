背景音乐目录（bgm.js 悬浮播放器读取这里）
============================================

当前曲目（在 bgm.js 的 TRACKS 清单中登记）：
    bgm-calm.mp3       自作 · 轻音乐
    bgm-gufeng.mp3     自作 · 古风
    bgm-beach.mp3      Beach2（热带海滩）   — PeriTune, CC-BY 4.0
    bgm-whisper.mp3    Whisper3（森林细语） — PeriTune, CC-BY 4.0
    bgm-raindrop.mp3   RainDrop（雨滴钢琴） — PeriTune, CC-BY 4.0
    bgm-ocean.mp3      Oceanfront（海滨）   — PeriTune, CC-BY 4.0

规则：
1. 增删曲目：改 bgm.js 顶部的 TRACKS 数组即可，文件放本目录；
   启动时会逐首 HEAD 探测，缺失的自动跳过，全部缺失才隐藏播放器。
2. 【版权】六首全部版权干净：两首「自作」为站长原创；其余四首来自
   PeriTune（https://peritune.com），采用 CC-BY 4.0 授权，可自由
   使用（含公网部署），条件是署名作者「PeriTune」。署名已写在
   本站免责声明页，新增 PeriTune 曲目时保持该署名即可。
   请勿再混入商业歌曲（流行歌、QQ/网易云下载曲目等）。
3. 建议参数：96~192kbps MP3。体积大会拖慢首次加载，
   新加曲目请尽量压在 5MB 以内。

替换/部署方法：覆盖本目录文件或改 TRACKS 后，在 PythonAnywhere
Web 面板 Reload 应用即可（STATIC_V 内容哈希自动换版本号，
用户端无需清缓存）。
