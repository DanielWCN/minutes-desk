# Changelog

Every release carries one number, shown next to the logo in the title bar and printed by
the black console window on start. Third digit for a fix, second for a new feature, first
for a change of shape. Earlier releases were stamped with a date instead, like
`2026-09-14a`; if that is what your title bar says, pull and run `python install.py`.
Numbering starts at v2.0.0, because by the time it started there was already a second
generation of the tool on people's machines.

版本号在标题栏 logo 右边，启动时的黑窗口里也会打印。第三位是修问题，第二位是加功能，
第一位是大改。之前用的是日期戳（例如 `2026-09-14a`）；如果你的标题栏还是日期，
拉一下代码再跑一次 `python install.py`。编号从 v2.0.0 起，因为开始编号的时候，大家机器上
跑的已经是第二代了。

## v2.0.1

A fix release, from a bug report by a colleague who put a 53 minute meeting through it.

- **Fixed: a long recording could hang and then die.** Transcription after the meeting handed
  the whole track to the recogniser in a single call instead of in five minute pieces. A 53
  minute call killed the recogniser outright with a native stack overflow; shorter ones
  survived but printed nothing for the entire run, so there was no way to tell working from
  hung. It now always works in five minute pieces, exactly as it does during the meeting.
  The words that come out are the same; the progress line every few minutes is new.
- **Fixed: waiting on the live transcriber no longer costs half an hour.** A live
  transcriber that had hung still looked alive, so analysis waited out its full thirty
  minute budget before doing the work itself. It now watches for real progress and stops
  waiting after fifteen minutes with nothing new.
- **Fixed: only the track that needs it is transcribed.** When one track was finished and
  the other was not, both were done again from scratch.
- **Fixed: a recording that ends on a fraction of a second no longer spins forever.**

修问题版，来自同事的一份 bug 报告：一场 53 分钟的会。

- **修复：录得久的会会卡住，然后直接挂掉。** 会后转写把整条音轨一次性丢给识别引擎，而不是按五分钟一段。
  53 分钟那一下直接把引擎打挂了（原生栈溢出）；短一点的虽然能跑完，但一整路不吐一个字，根本
  分不出是在干活还是死了。现在无论何时都按五分钟一段，和会议进行中一样。转出来的字一模一样，多出来的
  只是每几分钟一行的进度。
- **修复：等实时转写不再白白等半小时。** 卡死的实时转写看起来还是活的，分析就一直等，把三十分钟的额度
  等完了才自己动手。现在会看有没有真进展，十五分钟没动静就不等了。
- **修复：只转写真正需要转的那条音轨。** 以前一条已完成、一条没完成时，两条都从头重转。
- **修复：录音长度刚好多出不到半秒时，不会再无限循环。**

## v2.0.0

The first release with a version number instead of a date.

- **A name on every voice.** The far-end track is split by voice and each cluster is named
  from the way people address each other in the room, so the transcript says who spoke
  instead of `Speaker 1`. A voice it cannot place keeps its number and is marked, never
  guessed. Nothing about a voice is stored after the meeting.
- **The minutes are drafted for you.** When a local assistant is configured, the draft is
  written inside the processing chain, so the confirm desk opens on a filled page instead
  of a blank one.
- **The same minutes in the other language.** An English meeting also gets `minutes.zh.md`,
  translated item for item, with names, dates, owners and glossary terms left alone. The
  report has a switch in the top right. An existing translation is never overwritten.
- **A progress bar that means something.** The bar fills as steps of the chain finish, the
  step in flight is named, and the panel stays on screen when the run ends, in green with
  the total time. It no longer disappears and leaves you guessing.
- **The confirm desk.** The save button sits in the first heading row where it can be
  found, and a group with nothing to answer says so.
- Fixed: in Chinese, the verbatim transcript showed empty rows. An untranslated line now
  shows the words that were actually said.

第一个用版本号、不再用日期的版本。

- **每个声音都有名字。** 对端音轨按声音分开，再根据会上大家互相怎么称呼给每一簇配名字，
  逐字稿里写的是人名，不是 `Speaker 1`。认不准的保留编号并标注，绝不硬猜。会议结束后不留
  任何声音特征。
- **纪要自动起草。** 配了本地助手的话，草稿在处理流程里就写好了，打开校对台看到的是填好的
  一页，不是空白。
- **另一种语言的同一份纪要。** 英文会议会另外生成 `minutes.zh.md`，逐条对着翻，人名、日期、
  负责人和词库术语都不动。报告右上角可切换。已经存在的译文永不覆盖。
- **进度条是真的。** 流程每走完一步进度条就往前一格，正在跑的那一步会写出名字；跑完面板留在
  屏幕上，变成绿色并显示总耗时，不再一声不响地消失。
- **校对台。** 保存按钮挪到了第一组的标题行，找得到；没有需要回答的分组会直接说明。
- 修复：中文界面下逐字原文整片空白。没有译文的那一行现在显示当时说的原话。
