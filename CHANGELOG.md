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

## v2.4.9

- **Pressing "重新识别" did not re-read the captions.** The caption reader improved in
  v2.4.6, but a meeting that had already been processed kept the answer the old reader wrote -
  including its failure, `没有带名字的字幕行`, which was cached as though it were a finished
  result. So the one meeting that most needed the new reader was the one that never got it, and
  the transcript still said `Speaker 2`. The stored match now carries the version of the reader
  that wrote it; anything older is read again, and the names are worked out again with it.
- **Two sources agreeing is not a guess.** Where the captions put a name on a voice but not
  firmly enough to decide alone (a couple of seconds of overlap), and the model independently
  arrives at the same name from what people called each other in the room, that name is now
  settled instead of being printed as `Speaker 2 (Alice Chen?)`. Either source alone still is
  not enough, and neither of them is ever invented.

- **按「重新识别」没有重新读字幕。** v2.4.6 改好了字幕读取，但己经处理过的会议仍照用旧版得出的
  结果，连它的失败一起缓存（`没有带名字的字幕行`）。结果最需要新读法的那场会，恰恰永远用不上，
  记录里还是 `Speaker 2`。现在存下的字幕匹配会带上当时读取器的版本，旧的一律重读，名字也跟着重新定。
- **两个来源说法一致，就不再是推测。** 字幕把一个名字对到了某个声音上，但重合只有几秒、不足以单独
  定案；如果模型又从会上互相的称呼里得出同一个名字，这个名字现在直接算定，不再写成
  `Speaker 2 (Alice Chen?)`。单靠任何一边仍然不够，两边都不会凭空造名字。

## v2.4.8

- **Two meetings processed at once, and the second one died.** Re-processing one recording ten
  seconds after another loaded large-v3-turbo twice, ten threads and a batch of twelve each,
  and the second run stopped half a minute in with `RuntimeError: mkl_malloc: failed to
  allocate memory` - a wall of Python where a transcript should have been. Pressing both is a
  reasonable thing to do, so the second one now waits for the first to finish and prints one
  line saying so; nothing is refused and no click is lost. Only transcription queues, since
  that is the step that wants the memory.

- **两场会一起处理，第二场死了。** 隔十秒重新处理另一场，等于把 large-v3-turbo 装了两遍、
  各十个线程各 batch 12，第二场半分钟后停在 `RuntimeError: mkl_malloc: failed to allocate
  memory`，本该是逐字稿的地方是一堆 Python 报错。两场都想跑是很正常的要求，所以现在第二场
  等第一场跑完，日志里写一行说明；不拒绝、不丢点击。只有语音识别排队，因为吃内存的是它。

## v2.4.7

- **A traceback in the console window that looked like a crash and was not one.** Aborting a
  media request - clicking a key frame, closing the tab, or the video player deciding it has
  enough of screen.mp4 for now - makes Windows report WinError 10054, and socketserver prints
  the whole stack into the black window. The file streamer already ignored the two other ways
  a client can hang up; it now ignores this one too, and any remaining connection drop is
  logged as nothing instead of ten frames of Python. A real error still prints in full.

- **黑窗口里那段看着像崩了、其实没崩的报错。** 中途放弃一个媒体请求（点关键帧、关掉页签，
  或者播放器觉得 screen.mp4 先取这么多就够了）在 Windows 上报的是 WinError 10054，
  socketserver 就把整段调用栈打进黑窗口。文件流本来已经忽略客户端挂断的另外两种情况，
  现在这种也一起忽略，其余的连接中断也不再打栈。真出错还是照样完整打印。

## v2.4.6

Two things a real Zoom meeting broke, both measured on the recording of it.

- **Screen recording died 8 seconds into an 18 minute meeting.** gdigrab does not hand frames
  over at an even pace - it stalls, then delivers a burst to catch up - and each of those
  frames was stamped with the wall clock, so two of them landed a millisecond apart. libav
  then handed the mp4 muxer two packets with the same dts and the muxer answered EINVAL,
  which ended the capture: 580 KB of a 1102 second meeting. Frame times now sit on the frame
  grid, and a frame that arrives less than one frame early is folded into the one before it.
  Reproduced on synthetic arrival times (12 to 19 frames before it failed, three seeds out of
  three), fixed on the same ones, then confirmed against a live 16 second capture: 45 frames,
  15.3 seconds of video, no error. An encode or mux error no longer ends the recording either
  - one unwritable frame costs a frame, and the count is shown next to the size.
- **Captions were on, 340 lines were captured, and not one of them carried a name.** Zoom
  prints "Bob Kumar, yes exactly" as one accessibility node with the sentence beside it
  as another, and neither has a colon in it, so the speaker was dropped on the floor and
  naming fell back to inferring it from how people addressed each other. Caption rows are now
  put back together whether the speaker is glued on with nothing (Slack), a comma (Zoom) or
  any other separator, and the duplicate copy of the sentence is dropped. On that meeting:
  113 lines named, four speakers, no invented ones; the timing match went from "no caption
  line carries a name" to three voices pinned by the meeting's own captions, and the model,
  asked separately, named the same three. A meeting recorded before this release gets its
  names too, the next time it is processed - the text was already in the log.

真实 Zoom 会议暴露的两个问题，都在那场录音上量过。

- **录屏在 18 分钟的会里 8 秒就死了。** gdigrab 给帧的节奏并不均匀（先卡一下，再一次补几帧），
  而每帧都按墙上时钟打时间戳，于是有两帧只差 1 毫秒。libav 递给 mp4 封装器两个 dts 相同的包，
  封装器回 EINVAL，录屏就结束了：1102 秒的会只留下 580 KB。现在帧时间落在帧网格上，
  比一帧还早到的帧并进前一帧。先用合成的到达时间复现（三个种子都在 12 到 19 帧时失败），
  同样的输入修好，再用真机 16 秒录屏确认：45 帧、15.3 秒、无报错。写帧出错也不再终止录屏，
  一帧写不进去就只损失一帧，数量显示在尺寸旁边。
- **字幕明明开着、抓到了 340 行，却没有一行带名字。** Zoom 把「Bob Kumar, yes exactly」
  放在一个无障碍节点上，句子本身又单独放在旁边，两边都没有冒号，说话人就被丢掉了，
  认人只能退回到「听大家怎么互相称呼」。现在无论说话人是直接粘着句子（Slack）、用逗号隔开（Zoom）
  还是别的分隔符，字幕行都会被重新拼回去，重复的那份句子丢掉。在这场会上：113 行带上了名字，
  四个人，没有编造出来的名字；时间对齐从「没有带名字的字幕行」变成三个声音由会议字幕本身钉住，
  而模型单独判断给出的也是同样三个人。这次之前录的会，下次重新处理时也能拿到名字 ——
  文字本来就在日志里。

## v2.4.5

Three words from a real meeting, answered - and two of the three turned out to be defects
rather than vocabulary.

- **A contraction was being offered as a mis-hearing of a product name.** The transcript of a
  real meeting read `I couldn't [CloudFront?] catch, what is the plan`. The phonetic pass
  strips apostrophes before comparing, which is right for sound, but it also hid the word from
  the guard that throws out ordinary English: `couldn't` measures 5.09 on the zipf frequency
  scale and would have been dropped on sight, while the `couldnt` left after stripping measures
  3.43 and sailed through. `shouldn't` is 4.82 against 3.18, and every other contraction is the
  same shape. The guard now reads both forms and believes the commoner one.
- **Teaching the tool one more spelling of a term it already knew threw the rest away.** This
  machine's glossary merges on top of the shipped one key by key, and in `fix_after` a key
  holds the whole list of heard forms for that term. Adding one form of a term the shipped
  layer already covers replaced its list instead of extending it - one new variant of
  `headcount` would have silently dropped the other six, including both of the Chinese
  mis-hearings. Lists are merged now; a string still wins outright.
- **A login is not a name.** Pasting an invite's `To:` line is the fastest way to get the
  attendees in, but the page kept whatever sits before the `@`, which for some people is their
  name and for others is eight letters. It now looks the address up in the alias table the
  installer builds from the local calendar and puts the person's full name on the chip. An
  address written `first.last` is capitalised; an opaque login is left exactly as it is rather
  than dressed up as a surname. Your own address now drops out of the attendee list by name,
  where before it survived as a login and you appeared as a guest at your own meeting.
- Two terms went into this machine's private glossary as a result. Nothing in the shipped
  glossary changed.

Measured on the 26-minute meeting the three words came from: the confirm desk went from two
questions to none, both words now come out right on their own, and nothing else in the
transcript moved.

真实会议里挑出来的三个词，答完了 —— 其中两个根本不是词表问题，是代码的毛病。

- **一个英文缩略形式被当成产品名听错了。** 真实会议的逐字稿里写着
  `I couldn't [CloudFront?] catch, what is the plan`。发音比对之前会先去掉撇号，这对比声音是
  对的，但它同时也把这个词从「常见英文词就别猜了」那道闸门后面藏了起来：`couldn't` 的词频是
  5.09，本来一眼就该被扔掉；去掉撇号剩下的 `couldnt` 只有 3.43，于是顺顺当当过了闸。
  `shouldn't` 是 4.82 对 3.18，其他缩略形式全是一个样。现在两种写法都量，信更常见的那个。
- **给一个它本来就认识的词多教一种写法，会把原来的写法全丢掉。** 本机词表是按键覆盖在随程序
  发布的那份上面的，而 `fix_after` 里一个键存的是这个词的全部听错形式。给一个随程序发布的词
  加一种新写法，等于把它原来那张清单整个换掉 —— 给 `headcount` 加一条，会悄悄丢掉另外六条，
  包括两个中文误听。现在清单是合并的；值是字符串时仍然直接覆盖。
- **登录名不是名字。** 把会议邀请的 `To:` 行粘进来是把与会人一次填齐最快的办法，但页面留下的
  是 `@` 前面那一截，这对有些人是姓名，对有些人就是八个字母。现在它会去查安装时从本机日历建起
  来的别名表，把这个人的全名写到名牌上。写成 `first.last` 的地址会自动首字母大写；看不出名字的
  登录名就原样留着，不装成一个姓。你自己的地址现在会按名字从与会人里被剔掉，以前它以登录名的
  形态活了下来，于是你以客人的身份出现在自己的会议里。
- 顺带有两个词进了本机的私有词表。随程序发布的那份词表没有任何改动。

在这三个词出处的那场 26 分钟会议上实测：待确认台从两个问题变成零个，两个词现在自己就出对了，
逐字稿里别的地方一个字没动。

## v2.4.4

Captions were measured against a real Slack huddle for the first time, instead of against a
window with no captions in it. They were never being read at all, and the reason was in this
code, not in Slack.

- **Two of the skip rules were hiding the caption panel.** v2.4.1 added a list of Slack
  container classes the walk should not descend into, read off a real window - but a window
  with no huddle running. One of them, `c-virtual_list`, turns out to be the class of the
  caption list itself; another, `p-file_`, matches the container that wraps the entire huddle
  body. Between them the panel did not exist as far as the reader was concerned. Both are
  gone. The chat message list is still skipped, one level lower down, at `p-message_pane`.
- **The panel only says what it is in the label a person reads.** Its class and its automation
  id say nothing about captions; the tab panel is named 字幕 and the list inside it 转录. The
  hint that locks the reader on now looks at a container's name as well, which is also what
  lets it get past a skip rule legitimately. Names are trusted on containers only, so a chat
  message containing the word cannot open the sidebar to the walk.
- **A Slack caption row has no colon in it.** The speaker is glued straight onto the sentence -
  `Alice ChenMorning, everyone. This is Alice speaking.` - so the "Name: text" patterns
  matched nothing. Both halves are readable as separate text nodes underneath, so a row whose
  own text is exactly its children joined end to end is now rewritten as `Name: text`, and the
  children are dropped rather than filed a second time with nobody on them.
- **The panel's own pinned notice is not speech.** "Captions are being generated in English
  (US)" sits at the top of the caption list, which made it text inside a caption panel and
  therefore a line. It is dropped where it is read, before it can be recorded.
- `captions.meta.json` said `source: zoom-uia` for a Slack huddle. It now records `uia` and
  which clients were open.

Measured on the real huddle after the fix: the panel is found by name on the first scan, the
caption rows come out with the right speaker on them, and nothing else in the window does. One
limit worth knowing: a minimised window reads as nothing at all, so the meeting window can be
covered by other windows but not minimised.

第一次拿真实的 Slack huddle 测字幕，而不是拿一个没开字幕的窗口测。结论是：字幕从来就没被读到过，
原因在这份代码里，不在 Slack。

- **两条跳过规则把字幕面板藏起来了。** v2.4.1 加了一份 Slack 容器类名清单，让遍历不要走进去，
  是照着真窗口写的 —— 但那个窗口没在开 huddle。其中 `c-virtual_list` 恰恰就是字幕列表自己的
  类名，`p-file_` 又匹配到了包住整个 huddle 主体的那个容器。两条合起来，字幕面板对读取端根本
  不存在。两条都删了。聊天消息列表仍然跳过，只是位置下移一层，落在 `p-message_pane`。
- **这个面板只在给人看的标签上写明自己是什么。** 它的类名和 automation id 都不提字幕；标签页
  叫「字幕」，里面那个列表叫「转录」。现在锁定面板的线索也看容器的名字，这同时也是它能正当地
  绕过跳过规则的原因。名字只在容器上采信，所以一条含这个词的聊天消息不会把侧栏打开给遍历。
- **Slack 的字幕行里没有冒号。** 说话人直接粘在句子前面 ——
  `Alice ChenMorning, everyone. This is Alice speaking.` —— 所以「名字: 内容」那套正则一条
  都匹配不上。两半在它下面各自是一个可读的文本节点，因此：凡是自身文字恰好等于子节点首尾相接的
  行，现在会被重写成 `名字: 内容`，子节点直接丢掉，不再以「没有名字的一行」重复记一遍。
- **面板自己置顶的那条提示不是说话。** 「字幕正在以 English (US) 生成」挂在字幕列表顶端，于是它
  成了「字幕面板里的文字」，也就成了一行。现在在读到它的地方就丢掉，不给它被记下的机会。
- `captions.meta.json` 在 Slack huddle 上写的是 `source: zoom-uia`。现在写 `uia`，并记下当时
  开着哪些客户端。

改完在真 huddle 上实测：第一次扫描就按名字找到面板，字幕行带着正确的说话人出来，窗口里别的东西
一条都没进去。一个要知道的限制：窗口最小化之后什么都读不到，所以开会的窗口可以被别的窗口挡住，
但不能最小化。

## v2.4.3

The four files the caption work never touched (`report.py`, `llm.py`, `build.py`,
`minutes.py`, about 3,000 lines) put through the same review, on a copy of a real 26-minute
meeting rather than on `--help`. Two things were wrong, both in what the page told you.

- **The speaker appendix contradicted itself when captions did the naming.** Every row said
  "the meeting captions showed this name at this time", while the paragraph above them said
  names come from how people addressed each other and have to match the invite list. The
  paragraph is now written from the facts: it counts the people the captions named, says how
  many caption lines were read, and only describes the inference path for whoever is left.
- **A recording that transcribed nothing looked like a normal short meeting.** No line, no
  warning, just an empty transcript and "Duration 0 min". That is almost never a silent
  meeting; it is the loopback device or a muted mic, and the page was the one place you would
  have found out. There is now a banner that says so, and it stays quiet when the whole
  meeting was marked private, because there the empty transcript is the point.

Nothing else moved. What the review did check: `build.py` and `report.py` on a real session
(134 lines, 9 speakers), on a captions-only session, on a mixed one, and on a recording with
no speech at all; `llm.py --draft`, `--revise` and its mirror into the other language, all
end to end against the assistant engine; `minutes.py` front matter, sections, blocks and
scaffolding, plus four malformed inputs. No crash, no leaked handle, no swallowed error that
should have been raised, no division that can hit zero.

字幕那轮没碰过的四个文件（`report.py`、`llm.py`、`build.py`、`minutes.py`，约 3,000 行）
补做同样的审查，用的是一场真实 26 分钟会议的副本，不是 `--help`。发现两个问题，都在
页面对你说的话上。

- **字幕认出名字时，说话人附录自相矛盾。** 每一行都写着「会议字幕在这个时间显示的就是这个
  名字」，上面那段话却说名字来自会上互相的称呼、还要对得上受邀名单。现在这段话按事实写：
  数清楚有几个人是字幕直接给的、读到多少行字幕，剩下的人才讲推断那条路。
- **整场没转写出一句话时，页面看起来就像一场很短的正常会。** 没有提示，只有空的逐字稿和
  「Duration 0 min」。这几乎不可能是真的没人说话，而是「对方声音」选错了设备或者麦克风被
  系统静音了，而这一页是你唯一会发现它的地方。现在会有一条明确的提示；如果整场都被标成了
  私密，则不提示，因为那时逐字稿本来就该是空的。

其他没动。这轮真正测过的：`build.py` 和 `report.py` 跑真实会议（134 行、9 个说话人）、
纯字幕命名的会、字幕加推断混合的会、以及完全没有语音的录音；`llm.py --draft`、`--revise`
和它往另一种语言的同步，全部端到端连着 assistant 引擎跑通；`minutes.py` 的前置信息、分节、
块解析和空白模板，外加四种畸形输入。没有崩溃、没有泄漏的文件句柄、没有该抛却被吞掉的
错误、没有可能除零的地方。

## v2.4.2

A review of the whole caption path, and of what it touches. Seven things were wrong; all
seven are fixed, and each is checked by something that can be re-run.

- **The caption reader used to lock onto the wrong thing.** It decided it had found the panel
  by watching which piece of text kept changing into something long. Measured on a Slack window
  with no captions open anywhere in it, a status line redrawing itself while a file uploaded
  was enough: within thirty seconds the reader announced it had found the caption panel, and
  then filed window furniture as speech. Now the changing text has to carry a speaker on it,
  and the changes have to land inside half a minute of each other. Same window, same thirty
  seconds: nothing locks on, nothing is written. A real panel still locks on and still captures
  every line.
- **It also had no way to stop.** It ended when the recording wrote the file that says it
  finished. If the recorder was killed, or the window that launched it was closed, nothing ever
  wrote that file and the reader kept walking the accessibility tree for the rest of the day,
  burning a core for a meeting that ended hours ago. It now watches the recorder's heartbeat,
  which is rewritten twice a second, and stops 90 seconds after it goes quiet - or at once if a
  new recording has started.
- **A caption count that meant nothing.** An idle client wrote eleven lines of menu labels and
  notices in half a minute, so the panel showed captions arriving when none were. Those lines
  are gone, and the number on the panel now counts lines that actually name somebody. When text
  is being read but no name is on it, the panel says exactly that instead of showing a total
  that looks healthy.
- **`outlook.py` could not be run on its own.** Its `whoami()` had ended up below the block
  that calls it, so the file crashed with a NameError the moment it was executed directly. It
  worked when imported, which is why nothing else noticed. The rest of the codebase was scanned
  for the same mistake: this was the only one.
- **Two file handles leaked per recording.** The server opened a log for each of the two
  sidecars and never closed its copy, so on Windows the file stayed locked and deleting or
  archiving that session folder afterwards could fail with a sharing violation.
- **A window title is not a caption.** Chromium repeats the window title on its document node,
  so switching channel during a meeting filed the tab caption as a line of speech.
- **Live transcription could not stop either, and it costs far more.** It waited for the same
  file, so a killed recorder left it tailing a WAV that had stopped growing and re-running the
  speech model on it indefinitely. It now watches the same heartbeat. On a dead recorder it
  finishes in 18 seconds instead of never; 90 seconds of grace first, because ending early
  would truncate a transcript and that is the worse mistake.

- **字幕面板原来会认错地方。** 它靠「哪段文字一直在变成更长的句子」来判断自己找到了面板。在一个
  完全没开字幕的 Slack 窗口上实测：一个文件上传时不断刷新的状态行就够了 —— 三十秒内它就宣布
  「找到字幕面板了」，然后把界面文字当成说话记下来。现在变化的那段文字必须带着说话人的名字，
  而且几次变化要落在半分钟之内。同一个窗口、同样三十秒：不锁定、不写入。真的字幕面板照样锁得上，
  每一行照样抓得到。
- **它原来也不知道该什么时候停。** 它只在录音写下「我结束了」那个文件时结束。如果录音进程被杀掉，
  或者启动它的窗口被关掉，那个文件永远不会出现，它就会一整天继续遍历无障碍树，为一场几小时前就
  结束的会议烧着一个核。现在它盯录音那边每半秒刷新一次的心跳，心跳停了 90 秒就退出 —— 如果已经
  开始了新的录音，立刻退出。
- **一个没有意义的字幕计数。** 空闲的客户端半分钟就能写进十一行菜单标签和提示，于是面板上显示
  「字幕正在进来」，其实一条都没有。这些行没有了；面板上的数字现在只数真正带名字的行。当它读到了
  文字但上面没有名字时，面板会直接这么说，而不是给一个看起来很健康的总数。
- **`outlook.py` 没法单独运行。** 它的 `whoami()` 不知什么时候跑到了调用它的那段代码下面，于是
  直接执行这个文件就会 NameError。被 import 的时候是好的，所以一直没人发现。整个代码库扫过一遍
  同类问题：只有这一处。
- **每次录音漏两个文件句柄。** 服务端为两个旁路进程各开一个日志文件，自己那份从来没关，于是在
  Windows 上文件一直被占着，事后删除或归档那个会议文件夹可能会因为共享冲突失败。
- **窗口标题不是字幕。** Chromium 会把窗口标题重复挂在它的 document 节点上，于是开会时切一下频道，
  标签页标题就被当成一句话记了下来。
- **边录边转写也停不下来，而它贵得多。** 它等的是同一个文件，所以录音进程被杀掉之后，它会一直
  盯着一个已经不再增长的 WAV，反复在上面跑语音模型。现在它盯同一个心跳。录音已死的情况下，
  它 18 秒就结束，而不是永远不结束；先宽限 90 秒，因为提前结束会把逐字稿截断，那是更严重的错误。

## v2.4.1

- **Slack huddles are read the same way Zoom is.** Captions were only ever a Zoom feature
  here, which was an accident of where the first window happened to be. Nothing in the reader
  was actually Zoom-specific except a process name and how deep it looked, so both are now a
  table. One thing did have to change: Zoom draws its panel with native controls about six
  levels down, while Slack is a Chromium app whose captions are DOM nodes about twenty-five
  levels down. Measured on a real Slack window, the old depth limit of 20 reached 15 pieces of
  text and saw nothing that mattered; 32 reaches 132.
- **A Slack window is the whole client, not just the call.** The message list, the sidebar and
  the thread pane are all readable text with nothing to do with speech, and feeding a wall of
  chat to something looking for a caption panel is how it locks onto the wrong thing. The walk
  now stops at their doorstep, from classes read off a real window rather than guessed. That
  also made it six times cheaper: 0.10s a scan instead of 0.62s.
- The panel row, the setting and the note on the confirm desk no longer say "Zoom" when the
  names came from Slack.

- **Slack 的 huddle 现在和 Zoom 一样能读。** 之前字幕这件事只对 Zoom 生效，纯粹是因为最早拿来
  试的窗口是 Zoom。其实读取这一段里跟 Zoom 有关的只有两处：进程名，和往下看多深。现在这两处都
  变成了一张表。真正需要改的是深度：Zoom 用原生控件画字幕面板，大约在第六层；Slack 是 Chromium
  应用，字幕是 DOM 节点，大约在第二十五层。在真实的 Slack 窗口上实测，原来 20 层只能看到 15 段
  文字，什么有用的都没有；32 层能看到 132 段。
- **Slack 的窗口是整个客户端，不只是通话。** 消息列表、侧栏、话题面板全都是能读到的文字，却跟
  说话没有关系；把一墙聊天记录喂给一个正在找字幕面板的程序，它就会认错地方。现在遍历走到这些
  区域门口就停，用的是从真实窗口上读出来的类名，不是猜的。顺带快了六倍：一次扫描 0.10 秒，
  之前是 0.62 秒。
- 名字来自 Slack 的时候，录音面板那一行、设置里的开关、确认台上的说明，都不再写「Zoom」。

## v2.4.0

- **The far end's names can now come from Zoom itself.** Everything in the tool that puts a
  name on a voice has had to work it out afterwards, from how people address each other,
  because the loopback track arrives as one mixed stream that carries no names. Zoom is the
  one participant in the room that already knows: with captions on, it prints the roster name
  of whoever is speaking. While a meeting records, the tool now reads that panel through the
  Windows accessibility layer -- the same interface a screen reader uses -- and keeps one line
  per caption with the wall clock beside it. Nothing is captured that Zoom had not already
  drawn on the screen, no screen recording is involved, and the transcript is still the one
  this machine recognised: only the names and the times are taken.
- **The two timelines are lined up by measurement, not by assumption.** A caption carries a
  wall clock and a voice cluster carries seconds since the recording started, and the gap
  between them is not a constant you can look up. So every shift in a four-minute range is
  tried, and the one kept is the shift where each cluster overlaps one name instead of a
  smear of five. A shift that only wins by a hair wins nothing: the answer is then "the
  captions do not line up", and naming falls back to reading the conversation as before. On a
  real 26-minute team meeting, 6 of the 7 voices that could be named at all were settled this
  way, none of them wrongly.
- **Naming no longer needs a model at all when captions were running.** A name Zoom printed
  while a voice was talking is not an inference, so those clusters are not even shown to the
  model, and what is left is a shorter question. The recording panel says whether the caption
  panel is being read, and how many lines have come in, while the meeting is still going. The
  switch is in Settings and is on by default; with no caption panel open it writes nothing.

- **对端的名字现在可以直接来自 Zoom。** 工具里所有给声音配名字的办法，过去都只能事后推断 ——
  靠会上人们互相怎么称呼，因为系统回环那一路是混在一起的，本身不带名字。而会议里有一个参与者
  本来就知道答案：Zoom 打开字幕时，屏幕上显示的说话人名字直接来自会议名单。现在录音期间，工具
  会通过 Windows 的无障碍接口（读屏软件用的就是它）读那个字幕面板，把每一条字幕连同当时的
  时钟一起记下来。只记 Zoom 已经画在屏幕上的东西，不录屏；逐字稿仍然是本机识别的那一份，
  从字幕里只取名字和时间。
- **两条时间轴是量出来对齐的，不是假定的。** 字幕带的是墙上时钟，声音聚类带的是录音开始后的
  秒数，两者之间的差不是一个能查到的常数。所以程序会在正负两分钟内逐一试，留下让「每个聚类
  只压到一个名字」而不是「糊成五个」的那个偏移。只赢一点点的偏移不算赢：那时的结论是「字幕
  和音频对不上」，认人退回到原来读对话的办法。在一场 26 分钟的真实周会上，能认出名字的 7 个
  声音里有 6 个是这样定下来的，没有一个认错。
- **开着字幕的会，认人可以完全不用模型。** Zoom 在某个声音说话时打出的名字不是推断，所以这些
  聚类根本不会拿去问模型，剩下的问题也更短。录音面板上会实时写着字幕有没有在读、读到了多少行。
  开关在设置里，默认开着；没开字幕面板时它什么也不写。

## v2.3.4

- **Speakers get their names on the first press, and so do the minutes.** Two steps of the
  processing chain read files that later steps of the same chain write: naming reads the
  transcript that the build produces, and the draft reads the minutes file that the render
  scaffolds. On a fresh recording neither file existed yet, so both steps failed every time
  and only worked if you pressed again -- which is why a first transcript came back as
  "Speaker 1 .. Speaker 18" next to an all-TBD outline. The chain now builds and renders
  once up front. A session that already has those files pays nothing for it.
- **Naming can read a transcript that has already been split by voice.** It only recognised
  the far end while it still carried one label; once the build had turned that label into
  "Speaker 4", naming read the entire meeting as the microphone track, marked every line as
  the person recording, and then reported that nobody had been addressed by name all
  meeting. On a real 26-minute team meeting the same recording goes from 0 of 9 voices named
  to 7 named and 2 marked as guesses.
- **The notice about the far end no longer says the thing it now does is impossible.** With
  more than two people in the room the checklist used to state flatly that one mixed audio
  track cannot be told apart, so the minutes would not say who said what. That stopped being
  true when voice splitting and naming went in, and a notice claiming a working feature is
  impossible is worse than no notice: it stops the reader from ever reporting it broken. It
  now says how many voices were separated and how many got a name, and only asks for a check
  when something was a guess. It still warns, and asks for another pass, when the far end
  really has not been split.

- **第一次按下去就有名字，纪要也一起出来。** 处理链里有两步读的是后面步骤才写出来的文件：认人
  要读逐字稿，而逐字稿是再后面一步才生成的；起草纪要要读纪要文件，而那个文件是最后渲染时才铺出来
  的。新录的会上这两个文件都还不存在，所以这两步每次都失败，只有再按一次才会成功 —— 这就是为什么
  第一份逐字稿只有「Speaker 1 到 Speaker 18」，纪要里全是 TBD。现在处理链会先生成一次再往下走，
  已经有这些文件的会话不会多花时间。
- **认人现在读得懂已经按声音切过的逐字稿。** 以前它只认得对端还挂着同一个标签的样子；一旦生成
  步骤把那个标签换成了「Speaker 4」，认人就把整场会当成了麦克风那一路，把每一行都算成录音的人
  自己，然后得出「全场没有任何人被叫到名字」的结论。同一场 26 分钟的真实周会，9 个声音从 0 个
  认出来变成 7 个认出来、2 个标为推测。
- **「其他情况」里那张卡不再说「做不到」。** 会上超过两个人时，那张卡以前直接写「系统声音为一路
  混合音频，无法区分对端具体发言人，因此纪要不标注某某说」。分轨和认人做进来以后这句话就不成立了，
  而一张说「这个功能不可能」的提示比没有提示更糟 —— 它会让人永远不去报这个故障。现在它写的是分出了
  几个声音、其中几个认出了名字，只在有推测项时才请你核对；对端**真的**没分开时仍然是警告，并请你
  再跑一次 Analysis。

## v2.3.3

- **The border between the columns drags, instead of arming itself.** Press the left button
  and pull; let go and it stops. What it did before was worse and not on purpose: the drag
  took no pointer capture, and the right-hand column is an iframe, so the moment the pointer
  crossed into the minutes the parent window stopped hearing both the movement and the
  release. The border then kept following a mouse whose button was already up, which reads
  as "click to arm, move, click to drop". It now captures the pointer, and nothing else on
  the page can take it mid-drag. A right-click on the border no longer starts anything.
- Double click still puts a column back to the width the layout was drawn at.

- **两列之间那条边现在是按住拖，不是点一下就跟着走。** 按下左键拖，松手就停。之前那个行为不是
  设计，是缺陷：拖动没有抓住指针，而右边那一列是个 iframe，指针一进到纪要里，外层窗口就同时
  收不到移动、也收不到松手 —— 于是这条边继续跟着一只已经松开的鼠标走，用起来就像「点一下选中、
  移动、再点一下放下」。现在拖动全程独占指针，页面上别的东西抢不走。在这条边上点右键不再触发
  任何动作。
- 双击仍然把这一列恢复成排版时的宽度。

## v2.3.2

- **A sentence you typed yourself now reaches the other sheet too.** v2.3.1 carried a word
  across, which works because the same term is spelled the same in both sheets. A whole
  sentence cannot travel that way - the two sheets are in different languages - so it takes
  the model. The save is still instant; the page then chains one model call, about fifteen
  seconds, as a job with a log like every other model call in the tool. Fixing a single word
  still costs nothing and starts nothing.
- More than a dozen changes in one save is a rewrite, not a fix. It is not mirrored, and the
  log says so, rather than quietly reflowing the other sheet off an edit that large.
- On the copy-paste engine there is no model to call, so the banner says the other sheet did
  not follow, and which file to fix by hand.

- **自己打进去的整句话，现在也会带到另一份稿子上。** v2.3.1 带的是一个词 —— 同一个术语在两份
  稿子里拼法一样，所以照着替换就行。整句话没法这么带，两份稿子是两种语言，得让模型来。保存本身
  还是秒回；保存完页面自己接着花一次模型调用，大约十几秒，和工具里其他模型调用一样，是一个带日志
  的任务。只改一个词的时候，还是不花时间、也不会启动任何东西。
- 一次保存改了十几处以上，那是重写，不是修错。这种不自动同步，日志里会写明，而不是照着这么大的
  改动悄悄把另一份稿子也重排一遍。
- 复制粘贴那条引擎上没有模型可调，所以横幅会直接说另一份稿子没跟着改、要自己去改哪个文件。

## v2.3.1

- **The paste box checked the wrong headings.** Same bug as v2.2.2, in the other route: a
  rewritten Chinese sheet pasted back was refused for "缺少 ## Summary". It is now checked
  against the headings that file already has, and a genuinely broken paste is refused in the
  right language.
- **The paste box wrote the wrong file.** It always wrote `minutes.md`, so a revision of the
  Chinese page landed in the English one, in Chinese. It now writes the sheet the request
  came from, and says which file that is above the box.
- **Editing or pasting a sheet by hand also fixes the word in the other sheet.** Same
  word-level replace as a rewrite round, no model involved, so the copy-paste route and the
  editor behave like the button does.
- Saving over a sheet keeps a `.bak` now, like every other write. The paste box in
  particular holds a model's text, not yours.

- **粘回纪要校验的是错的标题。** 和 v2.2.2 同一个毛病，另一条路上还留着：中文稿改好粘回来，
  被判「缺少 ## Summary」。现在按这份文件本来的标题校验，真的粘错了也会用对的语言告诉你缺什么。
- **粘回纪要写错了文件。** 以前一律写 `minutes.md`，所以中文稿的修订会被写进英文那份里，内容还是
  中文。现在写你点进来的那份，框上面也写清是哪个文件。
- **手工编辑正文、手工粘回，同一个词在另一份稿子里也会一起改。** 和按钮那条路一样的词级替换，
  不调模型，所以复制粘贴那条路和编辑器现在跟按钮的行为一致。
- 保存覆盖正文时会留 `.bak` 了，和其他写入一样。粘回框里装的是模型的文字，不是你的。

## v2.3.0

- **A mark fixes both language sheets.** A mark is about the meeting, not about the page
  you happen to be reading: a term that is wrong in the Chinese minutes is wrong in the
  English ones too. When a round lands, the same fix is carried to the other sheet. A
  one-word fix - a term, a name, a system - is the same string in both files, so it is
  replaced directly, everywhere it stands on its own, at no cost and with no second model
  call. `17` inside `2026-09-17` is not that word, and neither is `P&L` inside `P&Ls`.
  Anything bigger than a word goes to the model once, with the other sheet and this
  round's before/after but *not* the transcript - the facts were settled one round ago -
  so it costs a few seconds rather than another full round.
- Every mirrored write goes through the same validation and the same `.bak` as a normal
  rewrite, and a failure is reported instead of retried: the other sheet is never left
  half-edited.
- The banner says what happened to the other sheet, with the count read back out of
  `review.json`.

- **标一处，两份稿子一起改。** 标注针对的是这场会，不是你正好在看的那一页 —— 中文稿里写错的
  术语，英文稿里同样是错的。一轮改完，同样的改动会带到另一份稿子上。如果改的是一个词（术语、
  人名、系统名），两份稿子里是同一个字符串，直接替换，凡是它独立成词的地方全都改，不花时间也
  不用再问一次模型；`2026-09-17` 里的 `17`、`P&Ls` 里的 `P&L` 不算独立成词，不会误伤。
  比一个词更大的改动，交给模型一次 —— 只给它另一份稿子和这一轮的前后对照，**不再发逐字稿**
  （事实上一轮已经定了），所以只多几秒，不是再来一整轮。
- 镜像过去的写入走的是和正常重写一样的校验、一样的 `.bak`；失败就报出来，不硬试，另一份稿子
  不会留下改了一半的样子。
- 提示条会说另一份稿子改了几处 —— 数字是从 `review.json` 读回来的，不是嘴上说的。

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
