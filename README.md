# Minutes Desk

A local meeting recorder, transcriber and minutes writer for Windows. It records both
sides of a call, transcribes on your own CPU, and hands you a finished page of minutes you
can paste straight into an email.

Nothing is uploaded. No cloud transcription service, no meeting-app plugin, no bot joining
the call, no host permission needed, and no notification to the other participants -
which is exactly why the section on **consent** below is not optional reading.

## Install

Two ways. Both end in the same place, and neither one asks you questions.

**If you use a coding agent** (Claude Code, Cursor, Copilot Agent, Codex or similar), paste
one line into it:

> Install Minutes Desk from `https://github.com/DanielWCN/minutes-desk` for me, following AGENTS.md.

The agent clones the repo and runs the installer. `AGENTS.md` is written for it, not for you.

**If you do not**, download the ZIP from the green *Code* button, unzip it anywhere, and
double-click **`Setup.bat`**. It puts Python packages and the speech model into a private
folder beside the code, writes your settings, and adds a shortcut to your Desktop. The
first run takes 10 to 20 minutes on a normal connection, almost all of it the 1.6 GB
model download.

Either way, when it finishes you double-click `Minutes Desk.bat` and record. There is no
setup wizard.

```
python install.py          # what both paths actually run
```

---

## Why it works with any meeting app

Audio is captured with a **WASAPI loopback tap**: a copy of whatever Windows is about to
send to your speakers. Zoom, Teams, Meet, Webex, a browser tab, a local video file - they
all pass through the same mixer, so the recorder does not care which one you use, does not
need an API, and cannot be detected by the app.

Two tracks are written, on purpose:

| file | what | who is speaking |
|---|---|---|
| `mic.wav` | your microphone | **always you.** No machine learning involved. |
| `others.wav` | loopback of the system output | everyone else |

A one-to-one call therefore needs no speaker diarization at all - the split is physical,
not statistical. For a larger meeting, attendee names come from the Outlook desktop client
already signed in on the machine, and you confirm who said what at a review step.

## What you get after a meeting

```
sessions/2026-05-04_1430_project-review/
  mic.wav  others.wav        the two tracks
  transcript.md              timestamped, both speakers, private ranges removed
  minutes.json               the facts: attendees, decisions, action items, open questions
  minutes.md                 the body, written by a language model (see below)
  minutes.html              one page, print-clean, with a "copy the email body" button
```

`minutes.html` is the deliverable. It is styled to look like the message the recipient
will actually receive, so what you proofread is what they read.

## Who writes the minutes

The tool ships with no language model inside it and never will: a meeting transcript is the
most sensitive thing it touches, so where it gets sent has to be your explicit, visible
choice. Three options, chosen in the settings page:

| engine | what happens | setup |
|---|---|---|
| **Hand it to your AI assistant** (default) | The tool copies one prompt to your clipboard. You paste it into whichever assistant you already use and paste the answer back. **No network request is made by this tool at all.** | none |
| Your own API | Any OpenAI-compatible `/chat/completions` endpoint - OpenAI, Azure, DeepSeek, Qwen, or your own gateway. | base URL, model, key |
| Ollama on this machine | The same HTTP shape at `127.0.0.1:11434`. Fully offline. | install Ollama, pull a model |

The first time you point it at an endpoint that is not on this machine, it says so and
asks you to confirm, once. That is deliberate friction, not a bug.

## The glossary, in two layers

A speech model that knows a term spells it right; one that does not writes nonsense which
then propagates into the minutes. So the tool keeps a vocabulary, in two files:

- `mmt/glossary.base.json` ships with the program and is **replaced on every update**.
  Public product names and ordinary business vocabulary only.
- `%LOCALAPPDATA%\MinutesDesk\glossary.user.json` is yours. Colleague names harvested from
  your own calendar, your team's jargon, everything the tool learned from your meetings.
  Never published, never overwritten.

They are merged at read time, with yours winning. Two destinations inside, and the
difference matters: `hotwords` is fed to the decoder *while it listens* and is capped,
because a long list makes recognition measurably **worse**; `fix_after` is applied to
finished text and is safe to grow without limit.

If you use an AI agent, `AGENTS.md` tells it how to seed the glossary with your team's
vocabulary and, more importantly, what it must not touch.

## Consent, and the law

This tool records other people without telling them. Whether you may do that depends on
where you are, where they are, and who employs you:

- Many jurisdictions require **all-party** consent for recording a conversation. Some
  require only one party. Some treat a work meeting differently from a private call.
- Your employer very likely has a policy on recording meetings and on where recordings and
  transcripts may be stored. That policy applies to this tool.
- Attendee names, what people said, and audio of their voice are personal data.

The tool helps where software can: the OneDrive archive is **off** until you turn it on,
a `PRIVATE` key cuts anything you say in a side conversation out of the transcript and the
minutes, and a purge command deletes a session's audio and text together. It cannot get
consent for you. Say at the top of the call that you are taking notes with an assistant.

## Requirements and limits

- **Windows 10/11.** The loopback tap and the Outlook read are Windows-specific. There is
  no macOS or Linux path and adding one is not a small change.
- **Python 3.10+**, and about 2.5 GB of disk for the environment and the speech model.
- No GPU needed. Transcription runs on the CPU at roughly 1.5x realtime on a mid-range
  laptop; a one-hour meeting is ready in around forty minutes, and it runs while you keep
  working because it deliberately yields CPU to the meeting app.
- **Outlook desktop client** for the attendee list. Without it, recording and
  transcription still work; you just type the names.
- Recording keeps the machine awake, but closing the lid can still suspend it. The tool
  detects a suspend that happened anyway and puts a loud banner on the minutes, because a
  silent hole in the audio is the worst possible failure.

## Living with it

| task | how |
|---|---|
| start | double-click `Minutes Desk.bat`, or the Desktop shortcut |
| re-run the self-checks | the settings page, any time |
| harvest calendar names again | `cd mmt && python firstrun.py --reseed` |
| see the merged glossary | `cd mmt && python lexicon.py` |
| rebuild one meeting's page | `cd mmt && python report.py "<session dir>" --me "Your Name"` |
| recover a crashed session | `cd mmt && python finalize.py --scan` |
| delete a meeting for good | `cd mmt && python purge.py "<session dir>"` |
| update | pull, then `python install.py` again. Your config and glossary live outside the program directory and survive. |

---

# 纪要台 Minutes Desk（中文）

Windows 上的本机会议纪要工具：录双轨、在自己电脑上转写、直接给你一份能贴进邮件的纪要。

**全部在本机完成**，不上传音频、不装会议插件、不需要主持人授权、不通知其他参会人。正因为
最后这一点，下面「录音的合法性」一节请务必读完。

## 安装

两条路，结果一样，两条都不会问你任何问题。

**你手上有 AI 助手**（Claude Code、Cursor、Copilot Agent 等）：把这一句贴给它就行。

> 帮我从 `https://github.com/DanielWCN/minutes-desk` 装 Minutes Desk，照 AGENTS.md 做。

它会自己拉代码、自己跑安装。`AGENTS.md` 是写给它看的，不是写给你看的。

**没有助手**：点绿色的 *Code* 按钮下载 ZIP，解到任意一个文件夹，双击里面的
**`Setup.bat`**。它把依赖和语音模型装到代码旁边一个独立目录里，写好配置，再在桌面放一个
快捷方式。第一次 10 到 20 分钟，大部分时间在下那个 1.6 GB 的模型。

两条路跑完之后都一样：双击 `Minutes Desk.bat`，开录。没有安装向导要你点。

```
python install.py          # 两条路实际上跑的都是这一句
```

## 为什么任何会议软件都能录

用 **WASAPI 环回采集**，录的是 Windows 即将送到你耳机里的那份声音。Zoom、Teams、飞书、
腾讯会议、浏览器里的网页会议、本地视频，都走同一个混音器，所以工具不关心你用哪个，也不
需要接口，会议软件也检测不到。

写两条轨，是刻意设计：`mic.wav` 是你的麦克风，**永远是你**，不需要任何模型判断；
`others.wav` 是系统输出的环回，就是别人。一对一的会议因此根本不需要说话人分离 —— 这个
切分是物理的，不是猜的。人多的会议，参会人名单从这台机器上已登录的 Outlook 客户端读，
最后有一步让你确认谁说了什么。

## 纪要正文由谁写

工具里不带模型，也不会带：逐字稿是它接触到的最敏感的东西，所以发到哪里必须是你明确、
看得见的选择。设置里三选一：

| 引擎 | 会发生什么 | 需要配置 |
|---|---|---|
| **交给 AI 助手**（默认） | 工具把提示词复制到剪贴板，你粘给自己在用的助手，再把回答贴回来。**工具本身不发起任何网络请求。** | 无 |
| 自带 API | 任何兼容 OpenAI 的接口都行。 | 地址、模型名、密钥 |
| 本机 Ollama | 同一种接口，跑在 `127.0.0.1:11434`，完全离线。 | 装 Ollama、拉模型 |

第一次把地址指向不在本机的服务时，它会明确告诉你并让你确认一次。这是故意加的摩擦。

## 词库分两层

模型认识一个术语就能写对，不认识就会写出一段胡话，而这段胡话会一路传到纪要里。所以工具
维护一份词库，分两个文件：`mmt/glossary.base.json` 随程序发布、**每次更新都会被替换**，
里面只有公开产品名和普通商务词汇；`%LOCALAPPDATA%\MinutesDesk\glossary.user.json` 是你
的，同事名字、团队术语、工具从你的会议里学到的东西都在这里，不发布、不会被更新覆盖。

读的时候两层合并，你的那层优先。里面 `hotwords` 是**边听边喂给解码器**的，有上限 ——
这个表太长会让识别变差；`fix_after` 作用在已经成文的文字上，可以无限增长。

如果你用 AI 助手，`AGENTS.md` 里写了它该怎么帮你把团队术语灌进去，以及哪些表它不许碰。

## 录音的合法性

这个工具会在不通知对方的情况下录音。你能不能这么做，取决于你在哪、对方在哪、以及你为谁
工作：很多地区要求**所有参与方**同意才可以录，有些只要求一方同意；你的雇主大概率对「会议
能不能录、录音和逐字稿能存在哪里」有明确规定，这些规定同样适用于本工具；参会人姓名、发言
内容、声音本身都属于个人信息。

软件能帮的部分它做了：OneDrive 归档默认**关闭**，要你自己打开；录音中按 `p` 可以把你在
旁边说的话整段从逐字稿和纪要里切掉；删除命令会把一场会的音频和文字一起删干净。它没法替你
取得同意 —— 开会时说一句「我用工具记一下纪要」。

## 环境要求

Windows 10/11（环回采集和读 Outlook 都是 Windows 专有的，没有 macOS/Linux 版本）；
Python 3.10 以上；环境加语音模型约占 2.5 GB 磁盘；**不需要显卡**，转写在 CPU 上大约
1.5 倍速，一小时的会四十分钟左右出结果，而且它刻意给会议软件让出 CPU，你可以边开会边跑。

## 日常使用

| 想做什么 | 怎么做 |
|---|---|
| 启动 | 双击 `Minutes Desk.bat`，或桌面快捷方式 |
| 重新跑自检 | 设置页，随时 |
| 重新扫一遍日历补人名 | `cd mmt && python firstrun.py --reseed` |
| 看合并后的词库 | `cd mmt && python lexicon.py` |
| 重新生成某场会的纪要页 | `cd mmt && python report.py "<会话目录>" --me "你的名字"` |
| 崩溃后救回一场会 | `cd mmt && python finalize.py --scan` |
| 彻底删除一场会 | `cd mmt && python purge.py "<会话目录>"` |
| 更新 | 拉一下代码，再跑一次 `python install.py`。配置和词库在程序目录之外，不会丢。 |
