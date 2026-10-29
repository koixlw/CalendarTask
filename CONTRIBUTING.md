# 参与 PyCal

感谢你愿意花时间改这个项目。

## 上手

```bash
git clone https://github.com/koixlw/CalendarTask.git
cd CalendarTask
python -m pip install -e .
python -m pytest tests/ -q
python run.py          # 起 GUI
```

需要 Python 3.10+。

## 跑测试

```bash
# pytest 风格的那部分 (数据层 / 同步 / 报告)
python -m pytest tests/ -q

# 脚本风格的那部分 (GUI 交互) —— 每个文件也能单独跑
python tests/test_toolbar_clicks.py
python tests/test_year_view.py
```

`pytest tests/` 会把脚本风格的那套也用子进程拉起来，不用手动挨个跑。

跑 GUI 测试时把平台设成 offscreen，不会开真窗口：

```bash
QT_QPA_PLATFORM=offscreen python -m pytest tests/ -q
```

## 代码风格

- 注释和文档字符串用中文，跟现有代码保持一致
- 类型标注用 `from __future__ import annotations` + 内建泛型（`str | None`）
- 面向用户的文案（按钮、报错、提示）一律中文
- 别引入新的运行时依赖 —— 打包出来的 exe 已经 155MB 了

## 提 PR

1. 从 `main` 切分支：`git checkout -b fix/xxx`
2. 改完跑一遍 `python -m pytest tests/ -q`
3. commit message 用中文写清楚改了什么
4. 开 PR，描述里写「改了什么 / 为什么 / 怎么验证」

## 打包

```bash
python build.py                # onedir, 启动快, 调试用
python build.py --onefile      # 单文件, 发布用
```

发布流程看 [`.github/workflows/release.yml`](.github/workflows/release.yml)：
打 `v*` 标签会自动打包并发 Release。

版本号只有一个地方要改：`pycal/__init__.py` 里的 `__version__`。
