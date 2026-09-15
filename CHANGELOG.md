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

## v2.2.2

- **Rewriting the Chinese minutes no longer fails on the way out.** The four headings were
  checked against the English ones, so a correct rewrite of `minutes.zh.md` (`## 摘要`,
  `## 决定`, ...) was rejected by this program, not by the model: 37 seconds of work thrown
  away with "缺少 ## Summary". A rewrite now has to keep the headings the file already had,
  whatever language they are in.
- **A rewrite takes about half as long.** The model is asked for the sentences that change,
  not for the whole file back: it still reads the entire transcript, but it types a few
  hundred characters instead of three thousand. Measured on a 33-minute meeting: 37s before,
  18-21s now. If a returned block cannot be placed in the file exactly once, the round is
  retried as a full rewrite rather than guessed at, so a badly quoted edit costs time and
  never lands in the wrong sentence.
- The same mistake in more than one place is fixed in all of them. A wrong name in a decision
  is usually a wrong name in the summary too, and asking twice for that was silly.
- The banner now names the file it kept a copy of: `minutes.zh.md.bak` when that is what you
  were reading.

- **中文纪要按标注重写不再白跑。** 校验器只认英文那四个标题，所以 `minutes.zh.md`（`## 摘要`、
  `## 决定`…）改对了反而被本程序判失败 —— 37 秒的活白干，还报「缺少 ## Summary」。现在的规则是：
  重写必须保留这份文件原本的标题，不管它是什么语言。
- **重写快了大约一半。** 现在只让模型交回要改的那几句，而不是整份文件重打一遍；逐字稿照样整份
  读完。33 分钟的会实测：以前 37 秒，现在 18-21 秒。如果交回来的块在文件里定位不唯一，这一轮
  改判为整篇重写，绝不猜 —— 宁可慢一次，也不许改错句子。
- 同一个错在别处也出现的话一起改。决定里人名错了，摘要里通常也错，让你标两次是没道理的。
- 提示条会说清备份的是哪个文件：你看的是中文那份，存的就是 `minutes.zh.md.bak`。

## v2.2.1

- **Minutes written before this release can be marked too.** The behaviour inside the paper
  is the document's own code, so a document rendered last week had no way to be marked no
  matter how new the app around it was: the bar invited you to drag across a sentence and
  nothing happened. A document now carries the version it was rendered with, and a stale one
  is re-rendered from `minutes.md` the moment it is opened. Same words, same layout, one
  extra second on the first open and nothing on the next.

- **以前的会议也能标了。** 划句子的能力住在纸里面，所以上周渲染出来的那份纪要，不管外面
  的程序多新，都划不动 —— 横条请你划，划了却没反应。现在每份文档都带着渲染它的版本号，
  旧的一打开就按 `minutes.md` 重排一次。字和排版都不变，第一次打开多花一秒，之后没有。

## v2.2.0

- **Mark what is wrong on the paper, and one button fixes exactly that.** Drag across a
  sentence in the minutes and a chip says "there is a problem here"; type one line about
  what is wrong and the sentence gets a yellow underline and a number. The bar above the
  paper then counts them and offers **rewrite from the marks**: the assistant re-reads the
  transcript and changes only the places you marked, leaving every other paragraph exactly
  as it was. It does not re-run recognition, so a session you have already confirmed stays
  confirmed and nothing takes minutes. The previous version is kept as `minutes.md.bak`.
  Marks live in `review.json` beside the minutes and survive a re-render, so you can mark
  five things and fix them in one round. If your engine has no command line the same button
  becomes **copy the revision request**, which is the old copy-and-paste route with the
  marks written into the prompt.
- **The assistant is asked for permission once.** Naming a program in `assistants.json` is
  not the same as agreeing that a meeting may be handed to it, so the first automatic write
  asks in plain words, once, and never again. The model behind that program is not
  necessarily on this machine, and the tool says so.

- **在纸上标出哪里不对，一个按钮就照着改。** 在纪要上划选一句话，会跳出「这里有问题」；写一行
  为什么，那句话就带上黄色下划线和编号。纸的上方会数出「N 处待修」，旁边就是**按标注重写**：
  助手重读一遍逐字稿，只改你标到的地方，其余段落原样保留。它不会重跑语音识别，所以已经确认过的
  会话仍然是确认过的，也不用等几分钟。上一版会存成 `minutes.md.bak`。标注存在纪要旁边的
  `review.json` 里，重排之后还在，所以可以一次标五处、一轮改完。如果你的引擎没有命令行，同一个
  按钮变成**复制修订请求**——还是原来的复制粘贴，只是提示词里带上了这些标注。
- **交给助手这件事只问一次。** 在 `assistants.json` 里写下一个程序的名字，不等于同意把会议内容
  交给它，所以第一次自动写正文时会明确问一次，之后不再问。那个程序背后的模型不一定在这台机器上，
  工具会把这句话说清楚。

## v2.1.0

- **Starting it again re-uses the tab you already have.** Double-clicking `Minutes Desk.bat`
  has always ended the previous copy of the program; now it does the same for the page. The
  tab already open notices the restart within about a second, reloads itself into the new
  version, and no second tab is opened on top of it. So the tabs stop piling up, and the
  page you are looking at can no longer be yesterday's HTML talking to today's Python,
  which was the one failure that made a dozen unrelated things look broken.

- **重新打开时用回你已经开着的那个标签页。** 双击 `Minutes Desk.bat` 一直会先结束上一份程序，
  现在页面也一样：已经开着的那一页大约一秒内就会发现工具重启了，自己刷成新版本，不会再另开一个。
  标签页不再越开越多，也不会再出现你看的是旧 HTML、背后是新 Python 这种情况——那一个问题
  会让十几个不相关的地方看起来都是坏的。

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
