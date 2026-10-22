背景音乐目录（bgm.js 悬浮播放器读取这里）
============================================

当前曲目（在 bgm.js 的 TRACKS 清单中登记）：
    bgm-calm.mp3       自作 · 轻音乐
    bgm-gufeng.mp3     自作 · 古风
    bgm-summer.mp3     Summer Melody（夏日旋律）
    bgm-afterrain.mp3  After The Rain（雨后）
    bgm-concert.mp3    她来听我的演唱会
    bgm-ocean.mp3      Oceanfront（海滨）

规则：
1. 增删曲目：改 bgm.js 顶部的 TRACKS 数组即可，文件放本目录；
   启动时会逐首 HEAD 探测，缺失的自动跳过，全部缺失才隐藏播放器。
2. 【版权提醒】本站部署在公网。两首「自作」和 PeriTune（CC-BY）
   可以放心用；商业歌曲（如张学友等）有版权风险，仅供私域学习
   场景，介意的话从 TRACKS 里删掉对应行即可，文件可留可删。
3. 建议参数：96~128kbps 单声道 MP3。体积大会拖慢首次加载，
   新加曲目请尽量压在 5MB 以内。

替换/部署方法：覆盖本目录文件或改 TRACKS 后，在 PythonAnywhere
Web 面板 Reload 应用即可（STATIC_V 内容哈希自动换版本号，
用户端无需清缓存）。
