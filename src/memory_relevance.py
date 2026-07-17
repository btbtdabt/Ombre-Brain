from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import jieba

from identity import identity_names
from query_terms import RECALL_SYSTEM_META_TERMS


DEFAULT_FACET_ALIASES = {
    "relationship_identity": (
        "human-ai relationship",
        "ai relationship",
        "ai companion",
        "digital companion",
        "relationship identity",
        "companion identity",
        "人机恋",
        "人机关系",
    ),
    "intimacy": (
        "intimacy",
        "亲密",
        "吻",
        "接吻",
        "亲吻",
        "亲亲",
        "拥吻",
        "欲望",
        "湿润",
        "发烫",
    ),
    "embodiment": (
        "embodiment",
        "embodied",
        "physical body",
        "physical form",
        "robot body",
        "avatar body",
        "具身",
        "身体",
        "形体",
    ),
    "hardware_protocol": (
        "hardware",
        "protocol",
        "bluetooth",
        "ble",
        "esp32",
        "mpr121",
        "bjd",
        "硬件",
        "协议",
        "蓝牙",
        "触摸模块",
        "触摸",
        "触碰",
        "铜箔",
    ),
    "communication_action": (
        "email",
        "e-mail",
        "mail",
        "gmail",
        "send email",
        "send mail",
        "message",
        "reply",
        "notify",
        "notification",
        "dm",
        "sms",
        "发邮件",
        "发信",
        "邮件",
        "邮箱",
        "回邮件",
        "回复邮件",
        "发消息",
        "私信",
        "短信",
        "通知",
        "联系",
    ),
    "career": (
        "career",
        "job search",
        "job hunting",
        "interview",
        "resume",
        "cv",
        "offer",
        "recruiter",
        "hr",
        "internship",
        "layoff",
        "resign",
        "resignation",
        "求职",
        "找工作",
        "找实习",
        "面试",
        "简历",
        "投递",
        "岗位",
        "招聘",
        "实习",
        "入职",
        "离职",
        "被裁",
        "裁员",
        "薪资",
        "工资",
        "offer",
        "hr",
    ),
    "old_or_resolved": (
        "old version",
        "legacy",
        "deprecated",
        "resolved",
        "obsolete",
        "superseded",
        "conflict",
        "blocked",
        "旧版",
        "旧方案",
        "以前",
        "之前",
        "已解决",
        "已合并",
        "已经合并",
        "已废弃",
        "废弃",
        "过时",
        "不再使用",
        "不应该继续",
        "冲突",
        "阻断",
    ),
}

DEFAULT_SECTION_HINTS: dict[str, tuple[str, ...]] = {}

DEFAULT_CONTEXT_TERMS = (
    "user",
    "assistant",
    "用户",
    "对方",
    "我",
    "你",
    "她",
    "他",
    "ta",
)

QUERY_DUPLICATED_CJK_FOLD_DENY_CHARS = frozenset("爸妈爷奶姥哥姐弟妹宝娃人天年")

STOPWORDS_DIR = Path(__file__).resolve().parent / "resources" / "stopwords"
QUERY_BASE_STOPWORDS_FILE = STOPWORDS_DIR / "query_base_stopwords.txt"
QUERY_KEEPWORDS_FILE = STOPWORDS_DIR / "query_keepwords.txt"
QUERY_EXTRA_STOPWORDS_FILE = STOPWORDS_DIR / "query_extra_stopwords.txt"


# P0's modular runtime may omit the historical resource directory. Keep the
# exact lexicons as deterministic fallbacks while preferring packaged files.
_FALLBACK_QUERY_BASE_STOPWORDS = frozenset(
    {
        "$",
        "0",
        "1",
        "2",
        "3",
        "4",
        "5",
        "6",
        "7",
        "8",
        "9",
        "?",
        "_",
        "“",
        "”",
        "、",
        "。",
        "《",
        "》",
        "一",
        "一些",
        "一何",
        "一切",
        "一则",
        "一方面",
        "一旦",
        "一来",
        "一样",
        "一般",
        "一转眼",
        "万一",
        "上下",
        "不仅",
        "不但",
        "不光",
        "不单",
        "不只",
        "不外乎",
        "不如",
        "不妨",
        "不尽",
        "不尽然",
        "不得",
        "不怕",
        "不惟",
        "不成",
        "不拘",
        "不料",
        "不是",
        "不比",
        "不然",
        "不特",
        "不独",
        "不管",
        "不至于",
        "不若",
        "不论",
        "不过",
        "不问",
        "与",
        "与其",
        "与其说",
        "与否",
        "与此同时",
        "且",
        "且不说",
        "且说",
        "两者",
        "个",
        "个别",
        "临",
        "为",
        "为了",
        "为什么",
        "为何",
        "为止",
        "为此",
        "为着",
        "乃",
        "乃至",
        "乃至于",
        "么",
        "之",
        "之一",
        "之所以",
        "之类",
        "乌乎",
        "乎",
        "乘",
        "也",
        "也好",
        "也罢",
        "了",
        "二来",
        "于",
        "于是",
        "于是乎",
        "云云",
        "云尔",
        "些",
        "亦",
        "人们",
        "人家",
        "什么",
        "什么样",
        "介于",
        "仍",
        "仍旧",
        "从",
        "从此",
        "从而",
        "他",
        "他人",
        "他们",
        "以",
        "以上",
        "以为",
        "以便",
        "以免",
        "以及",
        "以故",
        "以期",
        "以来",
        "以至",
        "以至于",
        "以致",
        "们",
        "任",
        "任何",
        "任凭",
        "似的",
        "但",
        "但凡",
        "但是",
        "何",
        "何以",
        "何况",
        "何处",
        "何时",
        "余外",
        "作为",
        "你",
        "你们",
        "使",
        "使得",
        "例如",
        "依",
        "依据",
        "依照",
        "便于",
        "俺",
        "俺们",
        "倘",
        "倘使",
        "倘或",
        "倘然",
        "倘若",
        "借",
        "假使",
        "假如",
        "假若",
        "傥然",
        "像",
        "儿",
        "先不先",
        "光是",
        "全体",
        "全部",
        "兮",
        "关于",
        "其",
        "其一",
        "其中",
        "其二",
        "其他",
        "其余",
        "其它",
        "其次",
        "具体地说",
        "具体说来",
        "兼之",
        "内",
        "再",
        "再其次",
        "再则",
        "再有",
        "再者",
        "再者说",
        "再说",
        "冒",
        "冲",
        "况且",
        "几",
        "几时",
        "凡",
        "凡是",
        "凭",
        "凭借",
        "出于",
        "出来",
        "分别",
        "则",
        "则甚",
        "别",
        "别人",
        "别处",
        "别是",
        "别的",
        "别管",
        "别说",
        "到",
        "前后",
        "前此",
        "前者",
        "加之",
        "加以",
        "即",
        "即令",
        "即使",
        "即便",
        "即如",
        "即或",
        "即若",
        "却",
        "去",
        "又",
        "又及",
        "及",
        "及其",
        "及至",
        "反之",
        "反而",
        "反过来",
        "反过来说",
        "受到",
        "另",
        "另一方面",
        "另外",
        "另悉",
        "只",
        "只当",
        "只怕",
        "只是",
        "只有",
        "只消",
        "只要",
        "只限",
        "叫",
        "叮咚",
        "可",
        "可以",
        "可是",
        "可见",
        "各",
        "各个",
        "各位",
        "各种",
        "各自",
        "同",
        "同时",
        "后",
        "后者",
        "向",
        "向使",
        "向着",
        "吓",
        "吗",
        "否则",
        "吧",
        "吧哒",
        "吱",
        "呀",
        "呃",
        "呕",
        "呗",
        "呜",
        "呜呼",
        "呢",
        "呵",
        "呵呵",
        "呸",
        "呼哧",
        "咋",
        "和",
        "咚",
        "咦",
        "咧",
        "咱",
        "咱们",
        "咳",
        "哇",
        "哈",
        "哈哈",
        "哉",
        "哎",
        "哎呀",
        "哎哟",
        "哗",
        "哟",
        "哦",
        "哩",
        "哪",
        "哪个",
        "哪些",
        "哪儿",
        "哪天",
        "哪年",
        "哪怕",
        "哪样",
        "哪边",
        "哪里",
        "哼",
        "哼唷",
        "唉",
        "唯有",
        "啊",
        "啐",
        "啥",
        "啦",
        "啪达",
        "啷当",
        "喂",
        "喏",
        "喔唷",
        "喽",
        "嗡",
        "嗡嗡",
        "嗬",
        "嗯",
        "嗳",
        "嘎",
        "嘎登",
        "嘘",
        "嘛",
        "嘻",
        "嘿",
        "嘿嘿",
        "因",
        "因为",
        "因了",
        "因此",
        "因着",
        "因而",
        "固然",
        "在",
        "在下",
        "在于",
        "地",
        "基于",
        "处在",
        "多",
        "多么",
        "多少",
        "大",
        "大家",
        "她",
        "她们",
        "好",
        "如",
        "如上",
        "如上所述",
        "如下",
        "如何",
        "如其",
        "如同",
        "如是",
        "如果",
        "如此",
        "如若",
        "始而",
        "孰料",
        "孰知",
        "宁",
        "宁可",
        "宁愿",
        "宁肯",
        "它",
        "它们",
        "对",
        "对于",
        "对待",
        "对方",
        "对比",
        "将",
        "小",
        "尔",
        "尔后",
        "尔尔",
        "尚且",
        "就",
        "就是",
        "就是了",
        "就是说",
        "就算",
        "就要",
        "尽",
        "尽管",
        "尽管如此",
        "岂但",
        "己",
        "已",
        "已矣",
        "巴",
        "巴巴",
        "并",
        "并且",
        "并非",
        "庶乎",
        "庶几",
        "开外",
        "开始",
        "归",
        "归齐",
        "当",
        "当地",
        "当然",
        "当着",
        "彼",
        "彼时",
        "彼此",
        "往",
        "待",
        "很",
        "得",
        "得了",
        "怎",
        "怎么",
        "怎么办",
        "怎么样",
        "怎奈",
        "怎样",
        "总之",
        "总的来看",
        "总的来说",
        "总的说来",
        "总而言之",
        "恰恰相反",
        "您",
        "惟其",
        "慢说",
        "我",
        "我们",
        "或",
        "或则",
        "或是",
        "或曰",
        "或者",
        "截至",
        "所",
        "所以",
        "所在",
        "所幸",
        "所有",
        "才",
        "才能",
        "打",
        "打从",
        "把",
        "抑或",
        "拿",
        "按",
        "按照",
        "换句话说",
        "换言之",
        "据",
        "据此",
        "接着",
        "故",
        "故此",
        "故而",
        "旁人",
        "无",
        "无宁",
        "无论",
        "既",
        "既往",
        "既是",
        "既然",
        "时候",
        "是",
        "是以",
        "是的",
        "曾",
        "替",
        "替代",
        "最",
        "有",
        "有些",
        "有关",
        "有及",
        "有时",
        "有的",
        "望",
        "朝",
        "朝着",
        "本",
        "本人",
        "本地",
        "本着",
        "本身",
        "来",
        "来着",
        "来自",
        "来说",
        "极了",
        "果然",
        "果真",
        "某",
        "某个",
        "某些",
        "某某",
        "根据",
        "欤",
        "正值",
        "正如",
        "正巧",
        "正是",
        "此",
        "此地",
        "此处",
        "此外",
        "此时",
        "此次",
        "此间",
        "毋宁",
        "每",
        "每当",
        "比",
        "比及",
        "比如",
        "比方",
        "没奈何",
        "沿",
        "沿着",
        "漫说",
        "焉",
        "然则",
        "然后",
        "然而",
        "照",
        "照着",
        "犹且",
        "犹自",
        "甚且",
        "甚么",
        "甚或",
        "甚而",
        "甚至",
        "甚至于",
        "用",
        "用来",
        "由",
        "由于",
        "由是",
        "由此",
        "由此可见",
        "的",
        "的确",
        "的话",
        "直到",
        "相对而言",
        "省得",
        "看",
        "眨眼",
        "着",
        "着呢",
        "矣",
        "矣乎",
        "矣哉",
        "离",
        "竟而",
        "第",
        "等",
        "等到",
        "等等",
        "简言之",
        "管",
        "类如",
        "紧接着",
        "纵",
        "纵令",
        "纵使",
        "纵然",
        "经",
        "经过",
        "结果",
        "给",
        "继之",
        "继后",
        "继而",
        "综上所述",
        "罢了",
        "者",
        "而",
        "而且",
        "而况",
        "而后",
        "而外",
        "而已",
        "而是",
        "而言",
        "能",
        "能否",
        "腾",
        "自",
        "自个儿",
        "自从",
        "自各儿",
        "自后",
        "自家",
        "自己",
        "自打",
        "自身",
        "至",
        "至于",
        "至今",
        "至若",
        "致",
        "般的",
        "若",
        "若夫",
        "若是",
        "若果",
        "若非",
        "莫不然",
        "莫如",
        "莫若",
        "虽",
        "虽则",
        "虽然",
        "虽说",
        "被",
        "要",
        "要不",
        "要不是",
        "要不然",
        "要么",
        "要是",
        "譬喻",
        "譬如",
        "让",
        "许多",
        "论",
        "设使",
        "设或",
        "设若",
        "诚如",
        "诚然",
        "该",
        "说来",
        "诸",
        "诸位",
        "诸如",
        "谁",
        "谁人",
        "谁料",
        "谁知",
        "贼死",
        "赖以",
        "赶",
        "起",
        "起见",
        "趁",
        "趁着",
        "越是",
        "距",
        "跟",
        "较",
        "较之",
        "边",
        "过",
        "还",
        "还是",
        "还有",
        "还要",
        "这",
        "这一来",
        "这个",
        "这么",
        "这么些",
        "这么样",
        "这么点儿",
        "这些",
        "这会儿",
        "这儿",
        "这就是说",
        "这时",
        "这样",
        "这次",
        "这般",
        "这边",
        "这里",
        "进而",
        "连",
        "连同",
        "逐步",
        "通过",
        "遵循",
        "遵照",
        "那",
        "那个",
        "那么",
        "那么些",
        "那么样",
        "那些",
        "那会儿",
        "那儿",
        "那时",
        "那样",
        "那般",
        "那边",
        "那里",
        "都",
        "鄙人",
        "鉴于",
        "针对",
        "阿",
        "除",
        "除了",
        "除外",
        "除开",
        "除此之外",
        "除非",
        "随",
        "随后",
        "随时",
        "随着",
        "难道说",
        "非但",
        "非徒",
        "非特",
        "非独",
        "靠",
        "顺",
        "顺着",
        "首先",
        "！",
        "，",
        "：",
        "；",
        "？",
    }
)
_FALLBACK_QUERY_KEEPWORDS = frozenset(
    {
        "猫",
        "狗",
        "妈妈",
        "妈",
        "工作",
        "面试",
        "哭",
        "焦虑",
        "委屈",
        "数据库",
    }
)
_FALLBACK_QUERY_EXTRA_STOPWORDS = frozenset(
    {
        "再试试",
        "再测试",
        "试试",
        "测试",
        "一下",
        "关于",
        "有关",
        "相关",
        "如果",
        "假如",
        "要是",
        "期望",
        "希望",
        "想要",
        "再",
        "还",
        "换种",
        "换个",
        "换一种",
        "换种说法",
        "换个说法",
        "换一种说法",
        "换种问法",
        "换个问法",
        "说法",
        "问法",
        "知道",
        "记得",
        "想到",
        "想起",
        "想起来",
        "联想",
        "联想到",
        "提到",
        "问到",
        "讲到",
        "说",
        "被",
        "得",
        "突然",
        "忽然",
        "今天",
        "明天",
        "现在",
        "当前",
        "今晚",
        "这次",
        "那次",
        "这个",
        "那个",
        "这条",
        "那条",
        "这",
        "那",
        "事情",
        "什么",
        "为什么",
        "怎么",
        "了吗",
        "吗",
        "呢",
        "啊",
        "呀",
        "啦",
        "吧",
        "的",
        "了",
        "是",
        "会",
        "能",
        "可以",
        "是不是",
        "有没有",
        "再来",
        "一次",
        "我",
        "你",
        "他",
        "她",
        "它",
        "我的",
        "你的",
        "他的",
        "她的",
        "它的",
        "自己",
        "自己的",
        "我家",
        "咱家",
        "有",
        "只",
        "个",
        "没",
        "和",
        "与",
        "及",
        "跟",
        "同",
        "不准",
        "搜",
        "记忆库",
        "没发图片",
        "发图片",
        "图片",
        "照片",
        "自动",
        "浮现",
        "注入",
        "召回",
        "记忆",
        "命中",
        "查到",
        "找到",
        "检测",
        "意见",
        "嗯",
        "呃",
        "唉",
        "诶",
        "欸",
        "哦",
        "噢",
        "自动浮现",
        "哭哭",
        "呜呜",
        "呜呜呜",
        "需要",
        "应该",
        "不应该",
        "是否",
        "要不要",
        "要不",
        "可不可以",
        "能不能",
        "需不需要",
        "试一下",
        "测试一下",
        "再试一下",
        "查一下",
        "找一下",
        "查查",
        "看看",
        "搜到",
        "回忆",
        "检索",
        "想到什么",
        "想起什么",
        "联想到什么",
        "联想什么",
        "还有",
        "或者",
        "以及",
        "还是",
        "怎么样",
        "怎样",
        "有吗",
        "有什么",
        "什么事",
        "发生",
        "发生了什么",
        "这张",
        "那张",
        "这张图",
        "这张图片",
        "这个图",
        "帖子",
        "消息",
        "这条帖子",
        "那条帖子",
        "这条消息",
        "那条消息",
        "回复",
        "回复一下",
        "回一下",
        "回个",
        "评论一下",
        "评论",
        "留言",
        "跟个",
        "跟一句",
        "说个",
        "说一句",
        "发个",
        "发一句",
        "补个",
        "补一句",
        "哈",
        "哈哈",
        "哈哈哈",
        "哈哈哈哈",
        "呵呵",
        "呵呵呵",
        "嘿嘿",
        "嘻嘻",
        "嗯嗯",
        "啊啊",
        "啊啊啊",
        "哇",
        "哇啊",
        "呜",
        "tt",
        "qwq",
        "qaq",
        "orz",
        "lol",
        "lmao",
        "xswl",
        "笑死我了",
        "你没事吧",
    }
)


def _load_stopword_file(path: Path) -> set[str]:
    try:
        text = path.read_text(encoding="utf-8-sig")
    except (OSError, UnicodeError):
        return set()
    return {
        line.strip()
        for line in text.splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    }


QUERY_BASE_STOPWORDS = frozenset(
    _load_stopword_file(QUERY_BASE_STOPWORDS_FILE) or _FALLBACK_QUERY_BASE_STOPWORDS
)
QUERY_KEEPWORDS = frozenset(
    _load_stopword_file(QUERY_KEEPWORDS_FILE) or _FALLBACK_QUERY_KEEPWORDS
)
QUERY_EXTRA_STOPWORDS = frozenset(
    _load_stopword_file(QUERY_EXTRA_STOPWORDS_FILE) or _FALLBACK_QUERY_EXTRA_STOPWORDS
)
QUERY_BASE_TOKEN_STOPWORDS = frozenset(
    term
    for term in QUERY_BASE_STOPWORDS
    if not re.fullmatch(r"[\u4e00-\u9fff]", term)
)
QUERY_WATER_STOPWORDS = frozenset(
    (QUERY_BASE_TOKEN_STOPWORDS | QUERY_EXTRA_STOPWORDS | RECALL_SYSTEM_META_TERMS)
    - QUERY_KEEPWORDS
)
QUERY_TERM_STOPWORDS = QUERY_WATER_STOPWORDS

DEFAULT_QUERY_EXPANSIONS = {
    "embodiment": ("hardware_protocol",),
}

DEFAULT_CONFLICTS = {
    "relationship_identity": ("intimacy",),
    "embodiment": ("intimacy",),
    "communication_action": ("hardware_protocol",),
}
CAREER_GENERIC_DIRECT_TERMS = frozenset({"工作"})
_FACETS_FOR_TEXT_CACHE: dict[tuple[Any, ...], tuple[tuple[str, float], ...]] = {}
_FACETS_FOR_TEXT_CACHE_MAX = 512
_FACETS_FOR_NODE_CACHE: dict[tuple, tuple[tuple[str, float], ...]] = {}
_FACETS_FOR_NODE_CACHE_MAX = 2048
_CONTENT_TERMS_CACHE: dict[tuple[Any, ...], tuple[str, ...]] = {}
_CONTENT_TERMS_CACHE_MAX = 512
PROTECTED_PHRASE_MAX_CHARS = 48
PROTECTED_PHRASE_PAIRS = (
    ("“", "”"),
    ('"', '"'),
    ("「", "」"),
    ("『", "』"),
    ("《", "》"),
    ("（", "）"),
    ("(", ")"),
    ("【", "】"),
    ("[", "]"),
    ("‘", "’"),
    ("`", "`"),
)

TECHNICAL_RECALL_STRONG_TERMS = frozenset(
    {
        "handoff",
        "bridge",
        "router",
        "gateway",
        "mcp",
        "recall",
        "rerank",
        "embedding",
        "bucket",
        "candidate",
        "edge",
        "diffusion",
        "injection",
        "注入",
        "召回",
        "检索",
        "扩散",
        "候选",
        "显式边",
    }
)

TECHNICAL_RECALL_SUPPORT_TERMS = frozenset(
    {
        "memory",
        "context",
        "image",
        "original",
        "raw",
        "记忆",
        "上下文",
        "图片",
        "读图",
        "原文",
    }
)

TECHNICAL_RECALL_EVIDENCE_TERMS = frozenset(
    {
        "original",
        "raw",
        "读图",
        "原文",
    }
)

ASSOCIATIVE_PROMPT_MARKERS = (
    "想到",
    "想起",
    "联想",
    "记得",
    "说",
    "提到",
    "问到",
    "讲到",
)

ASSOCIATIVE_PROMPT_VAGUE_FOCUS = {
    "你",
    "你会",
    "我",
    "我们",
    "这个",
    "那个",
    "什么",
}

ASSOCIATIVE_PROMPT_EMPTY_QUERY = {
    "会想到什么",
    "你会想到什么",
    "想到什么",
    "会想起什么",
    "你会想起什么",
    "想起什么",
    "联想到什么",
    "你会联想到什么",
}

EMOTIONAL_RECALL_TERMS = frozenset(
    {
        "开心",
        "高兴",
        "快乐",
        "幸福",
        "甜",
        "温柔",
        "感动",
        "激动",
        "安心",
        "舒服",
        "喜欢",
        "难过",
        "伤心",
        "痛苦",
        "委屈",
        "焦虑",
        "紧张",
        "担心",
        "烦",
        "烦躁",
        "生气",
        "愤怒",
        "害怕",
        "恐惧",
        "低落",
        "沮丧",
        "崩溃",
        "破防",
        "失落",
        "心疼",
        "想念",
        "吃醋",
        "不安",
        "孤独",
        "寂寞",
        "尴尬",
        "羞耻",
        "累",
        "疲惫",
        "emo",
        "sad",
        "happy",
        "angry",
        "tired",
        "anxious",
        "lonely",
        "upset",
    }
)
EMOTIONAL_RECALL_STATE_TERMS = frozenset(
    {
        "哭了",
        "哭",
        "想哭",
        "睡不着",
        "失眠",
        "心慌",
        "发抖",
        "难受",
        "崩溃",
        "破防",
    }
)
EMOTIONAL_RECALL_STANDALONE_STRONG_TERMS = frozenset({"想哭", "崩溃", "破防", "睡不着", "失眠", "心慌"})
EMOTIONAL_RECALL_TEMPORAL_TERMS = frozenset(
    {"今天", "昨天", "刚才", "刚刚", "这次", "那次", "现在", "今晚", "昨晚", "today", "tonight"}
)
EMOTIONAL_RECALL_REASON_TERMS = frozenset(
    {"为什么", "知道", "记得", "想起", "想起来", "原因", "因为", "怎么", "why", "know", "remember", "reason"}
)
EMOTIONAL_RECALL_EVENT_STOP_TERMS = (
    QUERY_WATER_STOPWORDS
    | EMOTIONAL_RECALL_TERMS
    | EMOTIONAL_RECALL_STATE_TERMS
    | {
        "有点",
        "一点",
        "一点点",
    }
)


@dataclass(frozen=True)
class MemoryRelevanceOptions:
    aliases: dict[str, tuple[str, ...]] = field(
        default_factory=lambda: {
            facet: tuple(values) for facet, values in DEFAULT_FACET_ALIASES.items()
        }
    )
    blocked_facets: frozenset[str] = frozenset()
    section_hints: dict[str, tuple[str, ...]] = field(default_factory=dict)
    context_terms: tuple[str, ...] = DEFAULT_CONTEXT_TERMS
    user_terms: tuple[str, ...] = ()


def _options_cache_key(options: MemoryRelevanceOptions) -> tuple[Any, ...]:
    return (
        tuple(
            sorted(
                (str(facet), tuple(str(alias) for alias in aliases))
                for facet, aliases in options.aliases.items()
            )
        ),
        tuple(sorted(str(facet) for facet in options.blocked_facets)),
        tuple(
            sorted(
                (str(section), tuple(str(facet) for facet in facets))
                for section, facets in options.section_hints.items()
            )
        ),
        tuple(str(term) for term in options.context_terms),
        tuple(str(term) for term in options.user_terms),
    )


@dataclass(frozen=True)
class RelevanceDecision:
    multiplier: float
    query_facets: dict[str, float]
    node_facets: dict[str, float]
    reasons: tuple[str, ...] = ()
    hard_block: bool = False

    @property
    def suppress(self) -> bool:
        return self.hard_block


@dataclass(frozen=True)
class RecallAdmissionDecision:
    admit: bool
    reason: str


@dataclass(frozen=True)
class EmotionalRecallPlan:
    triggered: bool
    strong_terms: tuple[str, ...] = ()
    weak_terms: tuple[str, ...] = ()
    event_terms: tuple[str, ...] = ()

    @property
    def search_terms(self) -> tuple[str, ...]:
        return tuple(_unique([*self.strong_terms, *self.event_terms, *self.weak_terms]))


def memory_relevance_options_from_config(config: dict | None = None) -> MemoryRelevanceOptions:
    aliases = {facet: list(values) for facet, values in DEFAULT_FACET_ALIASES.items()}
    section_hints = {key: list(values) for key, values in DEFAULT_SECTION_HINTS.items()}
    context_terms: list[str] = list(DEFAULT_CONTEXT_TERMS)
    user_terms: list[str] = []
    blocked: set[str] = set()

    identity_values = identity_names(config if isinstance(config, dict) else None)
    for key in ("ai_name", "user_name", "user_display_name", "user_aliases"):
        context_terms.extend(_list_text(identity_values.get(key)))
    for key in ("user_name", "user_display_name", "user_aliases"):
        user_terms.extend(_list_text(identity_values.get(key)))

    identity = (config or {}).get("identity", {}) if isinstance(config, dict) else {}
    if isinstance(identity, dict):
        for key in ("ai_name", "user_name", "user_display_name"):
            context_terms.extend(_list_text(identity.get(key)))
        context_terms.extend(_list_text(identity.get("user_aliases")))

    for cfg in _relevance_config_sections(config):
        _merge_alias_config(aliases, cfg.get("aliases"))
        _merge_facet_defs(aliases, cfg.get("facets"))
        _merge_section_hints(section_hints, cfg.get("section_hints"))
        blocked.update(_list_text(cfg.get("blocked_facets")))
        blocked.update(_list_text(cfg.get("disabled_facets")))
        context_terms.extend(_list_text(cfg.get("context_terms")))

    blocked = {str(facet).strip() for facet in blocked if str(facet).strip()}
    normalized_aliases = {}
    for facet, values in aliases.items():
        facet = str(facet).strip()
        if not facet or facet in blocked:
            continue
        normalized_aliases[facet] = tuple(_unique(_normalize_alias(value) for value in values))

    normalized_hints = {}
    for section, facets in section_hints.items():
        section = _normalize_section(section)
        if not section:
            continue
        kept = [facet for facet in _list_text(facets) if facet not in blocked]
        if kept:
            normalized_hints[section] = tuple(_unique(kept))

    return MemoryRelevanceOptions(
        aliases=normalized_aliases,
        blocked_facets=frozenset(blocked),
        section_hints=normalized_hints,
        context_terms=tuple(_unique(_normalize_alias(term) for term in context_terms)),
        user_terms=tuple(_unique(_normalize_alias(term) for term in user_terms)),
    )


def facets_for_text(
    text: str,
    options: MemoryRelevanceOptions | None = None,
) -> dict[str, float]:
    options = options or memory_relevance_options_from_config()
    raw_text = str(text or "")
    cache_key = (_options_cache_key(options), raw_text)
    cached = _FACETS_FOR_TEXT_CACHE.get(cache_key)
    if cached is not None:
        return dict(cached)
    scores = _facet_scores(
        (("text", str(text or ""), 1.0),),
        options,
    )
    if len(_FACETS_FOR_TEXT_CACHE) >= _FACETS_FOR_TEXT_CACHE_MAX:
        _FACETS_FOR_TEXT_CACHE.clear()
    _FACETS_FOR_TEXT_CACHE[cache_key] = tuple(scores.items())
    return scores


def facets_for_node(
    node: dict,
    options: MemoryRelevanceOptions | None = None,
) -> dict[str, float]:
    options = options or memory_relevance_options_from_config()
    meta = node.get("metadata", {}) if isinstance(node.get("metadata"), dict) else {}
    fields = (
        ("tags", _join_text(meta.get("tags")) + " " + _join_text(meta.get("bucket_tags")), 0.55),
        ("domain", _join_text(meta.get("domain")) + " " + _join_text(meta.get("bucket_domain")), 0.45),
        (
            "name",
            " ".join(
                [
                    str(node.get("name") or ""),
                    str(meta.get("name") or ""),
                    str(meta.get("bucket_name") or ""),
                ]
            ),
            0.4,
        ),
        (
            "text",
            " ".join(
                [
                    str(node.get("text") or ""),
                    str(node.get("content") or ""),
                    str(meta.get("summary") or ""),
                    str(meta.get("annotation_summary") or ""),
                    _join_evidence_spans(meta.get("evidence_spans")),
                ]
            )[:4000],
            0.5,
        ),
    )
    numeric_facets = _numeric_facets(meta.get("annotation_facets"))
    section = _normalize_section(node.get("section") or meta.get("section") or "")
    marks_old = _metadata_marks_old(meta)
    cache_key = (
        _options_cache_key(options),
        fields,
        tuple(sorted(numeric_facets.items())),
        section,
        marks_old,
    )
    cached = _FACETS_FOR_NODE_CACHE.get(cache_key)
    if cached is not None:
        return dict(cached)
    scores = _facet_scores(fields, options)
    for facet, value in numeric_facets.items():
        if facet in options.blocked_facets:
            continue
        scores[facet] = max(scores.get(facet, 0.0), value)

    for facet in options.section_hints.get(section, ()):
        if facet in options.blocked_facets:
            continue
        scores[facet] = max(scores.get(facet, 0.0), 0.6)

    if marks_old:
        scores["old_or_resolved"] = 1.0

    result = {facet: round(min(1.0, score), 3) for facet, score in scores.items()}
    if len(_FACETS_FOR_NODE_CACHE) >= _FACETS_FOR_NODE_CACHE_MAX:
        _FACETS_FOR_NODE_CACHE.clear()
    _FACETS_FOR_NODE_CACHE[cache_key] = tuple(result.items())
    return result


def active_facets(facets: dict[str, float], threshold: float = 0.45) -> set[str]:
    active = set()
    for facet, value in (facets or {}).items():
        try:
            if float(value) >= threshold:
                active.add(str(facet))
        except (TypeError, ValueError):
            continue
    return active


def query_has_facet(
    query: str,
    facet: str,
    options: MemoryRelevanceOptions | None = None,
) -> bool:
    return str(facet) in active_facets(facets_for_text(query, options))


def content_terms_for_query(
    query: str,
    options: MemoryRelevanceOptions | None = None,
) -> list[str]:
    options = options or memory_relevance_options_from_config()
    raw_query = str(query or "")
    cache_key = (_options_cache_key(options), raw_query)
    cached = _CONTENT_TERMS_CACHE.get(cache_key)
    if cached is not None:
        return list(cached)
    protected_phrases = extract_protected_phrases(raw_query)
    unprotected_query = strip_protected_phrases(raw_query) if protected_phrases else raw_query
    focus = recall_focus_query(query, options)
    topic = recall_topic_query(unprotected_query, options)
    terms: list[str] = []
    terms.extend(protected_phrases)
    if topic:
        terms.extend(_query_terms(topic, allow_single_cjk=True))
    if focus and focus != topic and _normalize_alias(focus) not in {
        _normalize_alias(phrase) for phrase in protected_phrases
    }:
        for term in _query_terms(focus):
            stripped = _strip_query_water_terms(term, options, include_context=False)
            if stripped != re.sub(r"[^0-9a-z\u4e00-\u9fff_.:-]+", "", _normalize_alias(term)):
                continue
            terms.append(term)
    content_terms = [
        term
        for term in terms
        if not _is_context_term(term, options.context_terms)
    ]
    result = _unique(content_terms or terms)
    if len(_CONTENT_TERMS_CACHE) >= _CONTENT_TERMS_CACHE_MAX:
        _CONTENT_TERMS_CACHE.clear()
    _CONTENT_TERMS_CACHE[cache_key] = tuple(result)
    return result


def extract_protected_phrases(query: str, *, max_chars: int = PROTECTED_PHRASE_MAX_CHARS) -> list[str]:
    text = str(query or "")
    if not text:
        return []
    phrases: list[tuple[int, str]] = []
    seen = set()
    for opener, closer in PROTECTED_PHRASE_PAIRS:
        pattern = re.compile(
            re.escape(opener) + rf"\s*(.{{1,{max_chars}}}?)\s*" + re.escape(closer),
            flags=re.DOTALL,
        )
        for match in pattern.finditer(text):
            value = _clean_protected_phrase(match.group(1), max_chars=max_chars)
            key = _normalize_alias(value)
            if not value or not key or key in seen:
                continue
            seen.add(key)
            phrases.append((match.start(), value))
    phrases.sort(key=lambda item: item[0])
    return [value for _start, value in phrases]


def strip_protected_phrases(query: str, replacement: str = " ") -> str:
    text = str(query or "")
    if not text:
        return ""
    for opener, closer in PROTECTED_PHRASE_PAIRS:
        pattern = re.compile(
            re.escape(opener)
            + rf"\s*.{{1,{PROTECTED_PHRASE_MAX_CHARS}}}?\s*"
            + re.escape(closer),
            flags=re.DOTALL,
        )
        text = pattern.sub(replacement, text)
    return text


def emotional_recall_plan(
    query: str,
    options: MemoryRelevanceOptions | None = None,
    *,
    max_terms: int = 4,
) -> EmotionalRecallPlan:
    options = options or memory_relevance_options_from_config()
    text = str(query or "").strip()
    if not text:
        return EmotionalRecallPlan(False)
    compact = _compact_emotional_text(text)
    if not compact or compact in {"哭", "哭了", "哭吗", "哭呢"}:
        return EmotionalRecallPlan(False)

    emotion_hits = _emotional_term_hits(compact, EMOTIONAL_RECALL_TERMS)
    state_hits = _emotional_term_hits(compact, EMOTIONAL_RECALL_STATE_TERMS)
    strong_terms: list[str] = []
    weak_terms: list[str] = []
    event_terms: list[str] = []

    for term, _start, _end in emotion_hits:
        if term not in weak_terms:
            weak_terms.append(term)

    for emotion, emotion_start, emotion_end in emotion_hits:
        for state, state_start, state_end in state_hits:
            if state == emotion:
                continue
            gap = max(state_start - emotion_end, emotion_start - state_end, 0)
            if gap > 8:
                continue
            for term in _emotional_compound_terms(
                compact,
                emotion,
                emotion_start,
                emotion_end,
                state,
                state_start,
                state_end,
            ):
                if term and term not in strong_terms:
                    strong_terms.append(term)
                    if len(strong_terms) >= max_terms:
                        break
            if len(strong_terms) >= max_terms:
                break
        if len(strong_terms) >= max_terms:
            break

    has_lookup_shell = _emotional_query_has_lookup_shell(text, compact)
    if has_lookup_shell:
        for state, _start, _end in state_hits:
            normalized_state = "哭" if state in {"哭", "哭了"} else state
            if normalized_state in EMOTIONAL_RECALL_STANDALONE_STRONG_TERMS and normalized_state not in strong_terms:
                strong_terms.append(normalized_state)

    if emotion_hits or state_hits:
        event_terms = _emotional_event_terms(text, options, compact, max_terms=max_terms)

    triggered = bool(strong_terms) or bool(event_terms and weak_terms) or bool(has_lookup_shell and weak_terms)
    if not triggered:
        return EmotionalRecallPlan(False)
    return EmotionalRecallPlan(
        True,
        strong_terms=tuple(_unique(strong_terms)[:max_terms]),
        weak_terms=tuple(_unique(weak_terms)[:max_terms]),
        event_terms=tuple(_unique(event_terms)[:max_terms]),
    )


def emotional_recall_terms(
    query: str,
    options: MemoryRelevanceOptions | None = None,
    *,
    include_weak: bool = False,
    max_terms: int = 4,
) -> list[str]:
    plan = emotional_recall_plan(query, options, max_terms=max_terms)
    if not plan.triggered:
        return []
    terms = [*plan.strong_terms, *plan.event_terms]
    if include_weak:
        terms.extend(plan.weak_terms)
    return _unique(terms)[:max_terms]


def recall_focus_query(
    query: str,
    options: MemoryRelevanceOptions | None = None,
) -> str:
    """Return the concrete anchor inside prompts like: 如果我说“X”，你会想到什么."""
    options = options or memory_relevance_options_from_config()
    text = str(query or "").strip()
    if not text:
        return ""
    compact = re.sub(r"[\s，。！？、,.!?:：;；~～（）()\[\]【】「」『』“”\"'`]+", "", text)
    if compact in ASSOCIATIVE_PROMPT_EMPTY_QUERY:
        return ""
    if not any(marker in text for marker in ASSOCIATIVE_PROMPT_MARKERS):
        return text

    quoted = re.search(r"[“\"'「『]([^”\"'」』]{1,32})[”\"'」』]", text)
    if quoted:
        focus = _clean_recall_focus(quoted.group(1))
        if focus:
            return focus

    memory_match = re.search(
        r"(?:记得|记不记得|还记得)(?P<focus>.+?)(?:的)?(?:那次|这次|时候|事情|事)(?:吗|么|嘛)?",
        text,
    )
    if memory_match:
        focus = _clean_recall_focus(memory_match.group("focus"))
        if focus:
            return _role_focus_term(focus) or focus

    speaker = _recall_speaker_pattern(options)
    patterns = (
        rf"^(?:如果|假如|要是)?\s*(?:{speaker})?\s*(?:说|提到|问到|讲到)\s*(?P<focus>.+?)(?:[，,。？?\s]*(?:你)?(?:会)?(?:想到|想起|联想到|联想|记得).*)?$",
        r"^(?P<focus>.+?)(?:[，,。？?\s]*(?:你)?(?:会)?(?:想到|想起|联想到|联想).*)$",
    )
    for pattern in patterns:
        match = re.match(pattern, text, flags=re.IGNORECASE)
        if not match:
            continue
        focus = _clean_recall_focus(match.group("focus"))
        if focus:
            return focus
    return text


def recall_topic_query(
    query: str,
    options: MemoryRelevanceOptions | None = None,
) -> str:
    """Return query text with recall shell/filler words removed."""
    options = options or memory_relevance_options_from_config()
    text = str(query or "").strip()
    if not text:
        return ""
    focus = recall_focus_query(text, options)
    focus_topic = _strip_query_water_terms(focus, options)
    raw_topic = _strip_query_water_terms(text, options)
    if focus != text and focus_topic and raw_topic and len(raw_topic) < len(focus_topic):
        return raw_topic
    return focus_topic or raw_topic


def recall_search_query(
    query: str,
    options: MemoryRelevanceOptions | None = None,
) -> str:
    options = options or memory_relevance_options_from_config()
    raw_query = str(query or "")
    focus_query = recall_focus_query(raw_query, options)
    if focus_query != raw_query.strip():
        return focus_query
    query_active = active_facets(facets_for_text(query, options))
    if "communication_action" not in query_active:
        return raw_query
    terms = content_terms_for_query(query, options)
    original_terms = _query_terms(query)
    if terms and terms != original_terms:
        return " ".join(terms)
    return raw_query


def query_has_explicit_entity_marker(query: str) -> bool:
    text = str(query or "").strip()
    if not text:
        return False
    if re.search(r"\b0x[0-9a-fA-F]+\b", text):
        return True
    if re.search(r"\b[A-Za-z]+/[A-Za-z0-9._-]+\b", text):
        return True
    if re.search(r"\d", text):
        return True
    if re.search(r"\b[A-Z0-9][A-Z0-9._:/-]{2,}\b", text):
        return True
    title_matches = list(re.finditer(r"\b[A-Z][a-z][A-Za-z0-9._:-]{1,}\b", text))
    if not title_matches:
        return False
    words = re.findall(r"\b[A-Za-z0-9._:-]+\b", text)
    if len(words) == 1:
        return True
    if len(title_matches) >= 2:
        return True
    match = title_matches[0]
    if match.start() > 0:
        return True
    leading_word = match.group(0).lower()
    sentence_starters = {
        "add",
        "can",
        "check",
        "could",
        "create",
        "did",
        "do",
        "does",
        "explain",
        "find",
        "fix",
        "help",
        "how",
        "is",
        "let",
        "lets",
        "look",
        "make",
        "maybe",
        "open",
        "please",
        "read",
        "remove",
        "run",
        "should",
        "tell",
        "today",
        "update",
        "was",
        "were",
        "what",
        "when",
        "where",
        "who",
        "why",
        "would",
        "write",
    }
    if leading_word in sentence_starters:
        return False
    return True


def query_has_technical_recall_marker(query: str) -> bool:
    text = str(query or "").lower()
    if any(term in text for term in TECHNICAL_RECALL_STRONG_TERMS):
        return True
    support_hits = {term for term in TECHNICAL_RECALL_SUPPORT_TERMS if term in text}
    return len(support_hits) >= 2 and bool(support_hits & TECHNICAL_RECALL_EVIDENCE_TERMS)


def recall_admission_decision(
    query: str,
    node: dict,
    options: MemoryRelevanceOptions | None = None,
    *,
    semantic_score: float | None = None,
    rerank_score: float | None = None,
    high_confidence_edge: bool = False,
    semantic_threshold: float = 0.72,
    rerank_threshold: float = 0.65,
) -> RecallAdmissionDecision:
    options = options or memory_relevance_options_from_config()
    if not query_has_explicit_entity_marker(query):
        return RecallAdmissionDecision(True, "non_explicit_query")
    if _has_direct_query_evidence(query, node, options):
        return RecallAdmissionDecision(True, "topic_evidence")
    if _safe_float(semantic_score) >= semantic_threshold:
        return RecallAdmissionDecision(True, "strong_semantic")
    if _safe_float(rerank_score) >= rerank_threshold:
        return RecallAdmissionDecision(True, "strong_rerank")
    if high_confidence_edge:
        return RecallAdmissionDecision(True, "high_confidence_direct_edge")
    return RecallAdmissionDecision(False, "explicit_query_without_reliable_evidence")


def relevance_decision(
    query: str,
    node: dict,
    options: MemoryRelevanceOptions | None = None,
) -> RelevanceDecision:
    options = options or memory_relevance_options_from_config()
    query_facets = facets_for_text(query, options)
    node_facets = facets_for_node(node, options)
    query_active = active_facets(query_facets)
    node_active = active_facets(node_facets, threshold=0.3)
    if not query_active:
        return RelevanceDecision(1.0, query_facets, node_facets)

    reasons = []
    direct_query_evidence = _has_direct_query_evidence(query, node, options)
    career_direct_query_evidence = _has_direct_query_evidence(
        query,
        node,
        options,
        ignored_terms=CAREER_GENERIC_DIRECT_TERMS,
    )
    overlap = query_active & node_active

    if "old_or_resolved" in node_active and "old_or_resolved" not in query_active:
        if not direct_query_evidence:
            return RelevanceDecision(
                0.0,
                query_facets,
                node_facets,
                ("old_or_resolved_conflict",),
                hard_block=True,
            )
        reasons.append("old_or_resolved_demoted")

    for query_facet, blocked_node_facets in DEFAULT_CONFLICTS.items():
        if query_facet not in query_active:
            continue
        conflicts = [
            facet
            for facet in blocked_node_facets
            if facet in node_active and facet not in query_active
        ]
        if conflicts:
            conflict_reasons = tuple(f"{query_facet}_vs_{facet}" for facet in conflicts)
            if "intimacy" in conflicts:
                return RelevanceDecision(
                    0.0,
                    query_facets,
                    node_facets,
                    conflict_reasons,
                    hard_block=True,
                )
            if not direct_query_evidence:
                return RelevanceDecision(
                    0.0,
                    query_facets,
                    node_facets,
                    conflict_reasons,
                    hard_block=True,
                )
            reasons.extend(f"{reason}_demoted" for reason in conflict_reasons)

    if overlap:
        multiplier = 1.25
        if "intimacy" in overlap:
            multiplier = 1.35
        reasons.append("facet_overlap")
    else:
        multiplier = 1.0

    if "old_or_resolved" in query_active and "old_or_resolved" in node_active:
        multiplier = max(multiplier, 1.2)
        reasons.append("old_or_resolved_requested")
    elif reasons and any(reason.endswith("_demoted") for reason in reasons):
        multiplier = min(multiplier, 0.65)

    if (
        "communication_action" in query_active
        and "communication_action" not in node_active
        and not direct_query_evidence
    ):
        multiplier = min(multiplier, 0.45)
        reasons.append("communication_action_missing_demoted")

    if "career" in query_active and "career" not in node_active and not career_direct_query_evidence:
        return RelevanceDecision(
            0.0,
            query_facets,
            node_facets,
            ("career_missing",),
            hard_block=True,
        )

    return RelevanceDecision(multiplier, query_facets, node_facets, tuple(reasons))


def relevance_multiplier(
    query: str,
    node: dict,
    options: MemoryRelevanceOptions | None = None,
) -> float:
    return relevance_decision(query, node, options).multiplier


def should_suppress_candidate(
    query: str,
    node: dict,
    options: MemoryRelevanceOptions | None = None,
) -> bool:
    return relevance_decision(query, node, options).suppress


def recall_rank(
    query: str,
    node: dict,
    options: MemoryRelevanceOptions | None = None,
) -> tuple[int, float]:
    options = options or memory_relevance_options_from_config()
    query_active = active_facets(facets_for_text(query, options))
    node_active = active_facets(facets_for_node(node, options), threshold=0.3)
    try:
        score = float(node.get("score", 0.0))
    except (TypeError, ValueError):
        score = 0.0

    if "embodiment" in query_active:
        if "intimacy" in node_active and "intimacy" not in query_active:
            return 50, -score
        if "embodiment" in node_active:
            return 0, -score
        if "hardware_protocol" in node_active:
            return 1, -score
    if "hardware_protocol" in query_active and "hardware_protocol" in node_active:
        return 0, -score
    if "communication_action" in query_active:
        if "hardware_protocol" in node_active and "hardware_protocol" not in query_active:
            return 5, -score
        if "communication_action" in node_active:
            return 0, -score
        return 15, -score
    if "career" in query_active:
        if _direct_query_evidence_terms(
            query,
            node,
            options,
            ignored_terms=CAREER_GENERIC_DIRECT_TERMS,
        ):
            return 0, -score
        if "career" in node_active:
            return 4, -score
        return 18, -score
    if "relationship_identity" in query_active and "relationship_identity" in node_active:
        return 0, -score
    if "intimacy" in query_active and "intimacy" in node_active:
        return 0, -score
    return 20, -score


def expanded_terms_for_query(
    query: str,
    options: MemoryRelevanceOptions | None = None,
) -> list[str]:
    options = options or memory_relevance_options_from_config()
    expanded = []
    query_active = active_facets(facets_for_text(query, options))
    for facet in sorted(query_active):
        expanded.extend(options.aliases.get(facet, ()))
        for related_facet in DEFAULT_QUERY_EXPANSIONS.get(facet, ()):
            expanded.extend(options.aliases.get(related_facet, ()))
    return _unique(expanded)


def _relevance_config_sections(config: dict | None) -> list[dict]:
    if not isinstance(config, dict):
        return []
    sections = []
    for key in ("recall_facets", "memory_relevance"):
        value = config.get(key)
        if isinstance(value, dict):
            sections.append(value)
    return sections


def _merge_alias_config(target: dict[str, list[str]], raw: Any) -> None:
    if not isinstance(raw, dict):
        return
    for facet, values in raw.items():
        facet = str(facet).strip()
        if not facet:
            continue
        target.setdefault(facet, []).extend(_list_text(values))


def _merge_facet_defs(target: dict[str, list[str]], raw: Any) -> None:
    if not isinstance(raw, dict):
        return
    for facet, definition in raw.items():
        if isinstance(definition, dict):
            values = definition.get("aliases") or definition.get("phrases") or []
        else:
            values = definition
        facet = str(facet).strip()
        if facet:
            target.setdefault(facet, []).extend(_list_text(values))


def _merge_section_hints(target: dict[str, list[str]], raw: Any) -> None:
    if not isinstance(raw, dict):
        return
    for section, facets in raw.items():
        section = _normalize_section(section)
        if section:
            target.setdefault(section, []).extend(_list_text(facets))


def _facet_scores(
    fields: tuple[tuple[str, str, float], ...],
    options: MemoryRelevanceOptions,
) -> dict[str, float]:
    scores: dict[str, float] = {}
    for facet, aliases in options.aliases.items():
        if facet in options.blocked_facets:
            continue
        score = 0.0
        for _field_name, raw_text, weight in fields:
            score += _alias_match_score(raw_text, aliases) * weight
            if score >= 1.0:
                break
        if score > 0:
            scores[facet] = round(min(1.0, score), 3)
    return scores


def _numeric_facets(raw: Any) -> dict[str, float]:
    if not isinstance(raw, dict):
        return {}
    facets = {}
    for key, value in raw.items():
        try:
            score = float(value)
        except (TypeError, ValueError):
            continue
        key = str(key).strip()
        if key:
            facets[key] = max(0.0, min(1.0, score))
    return facets


def _join_evidence_spans(raw: Any) -> str:
    if not isinstance(raw, list):
        return ""
    parts = []
    for item in raw:
        if isinstance(item, dict):
            text = item.get("text") or item.get("span") or ""
        else:
            text = item
        if str(text).strip():
            parts.append(str(text))
    return " ".join(parts)


def _alias_match_score(text: str, aliases: tuple[str, ...]) -> float:
    normalized = _normalize_text(text)
    if not normalized:
        return 0.0
    score = 0.0
    for alias in aliases:
        alias = _normalize_alias(alias)
        if not alias:
            continue
        if _contains_alias(normalized, alias):
            score += 0.65
        if score >= 1.0:
            break
    return min(1.0, score)


def _contains_alias(text: str, alias: str) -> bool:
    if re.fullmatch(r"[a-z0-9][a-z0-9_\- ]*[a-z0-9]", alias):
        pattern = rf"(?<![a-z0-9_]){re.escape(alias)}(?![a-z0-9_])"
        return re.search(pattern, text) is not None
    return alias in text


def _metadata_marks_old(meta: dict) -> bool:
    return bool(
        meta.get("resolved")
        or meta.get("digested")
        or meta.get("bucket_resolved")
        or meta.get("bucket_digested")
    )


def _has_direct_query_evidence(
    query: str,
    node: dict,
    options: MemoryRelevanceOptions,
    *,
    ignored_terms: frozenset[str] | set[str] | tuple[str, ...] = (),
) -> bool:
    return bool(_direct_query_evidence_terms(query, node, options, ignored_terms=ignored_terms))


def _direct_query_evidence_terms(
    query: str,
    node: dict,
    options: MemoryRelevanceOptions,
    *,
    ignored_terms: frozenset[str] | set[str] | tuple[str, ...] = (),
) -> list[str]:
    node_text = _normalize_text(_node_text(node))
    if not node_text:
        return []
    ignored = {_normalize_alias(term) for term in ignored_terms or ()}
    matched = []
    for term in content_terms_for_query(query, options):
        normalized = _normalize_alias(term)
        if normalized in ignored:
            continue
        if _contains_alias(node_text, normalized):
            matched.append(term)
    return matched


def _node_text(node: dict) -> str:
    if not isinstance(node, dict):
        return ""
    meta = node.get("metadata", {}) if isinstance(node.get("metadata"), dict) else {}
    return " ".join(
        [
            str(node.get("text") or ""),
            str(node.get("content") or ""),
            str(node.get("name") or ""),
            str(meta.get("name") or ""),
            str(meta.get("bucket_name") or ""),
            str(meta.get("summary") or ""),
            str(meta.get("annotation_summary") or ""),
            _join_evidence_spans(meta.get("evidence_spans")),
            _join_text(meta.get("tags")),
            _join_text(meta.get("bucket_tags")),
            _join_text(meta.get("domain")),
            _join_text(meta.get("bucket_domain")),
        ]
    )


def _is_context_term(term: str, context_terms: tuple[str, ...]) -> bool:
    normalized = _normalize_alias(term)
    return bool(normalized and normalized in set(context_terms or ()))


def _clean_recall_focus(value: Any) -> str:
    focus = re.sub(r"\s+", "", str(value or "").strip())
    focus = re.sub(r"^[，。！？、,.!?:：;；~～（）()\[\]【】「」『』“”\"'`]+", "", focus)
    focus = re.sub(r"[，。！？、,.!?:：;；~～（）()\[\]【】「」『』“”\"'`]+$", "", focus)
    if not focus:
        return ""
    if len(focus) > 32:
        return ""
    normalized = _normalize_alias(focus)
    if normalized in ASSOCIATIVE_PROMPT_VAGUE_FOCUS:
        return ""
    if normalized.endswith("会") and len(normalized) <= 3:
        return ""
    if any(marker in normalized for marker in ("想到什么", "想起什么", "联想到什么", "联想什么")):
        return ""
    return focus


def _clean_protected_phrase(value: Any, *, max_chars: int = PROTECTED_PHRASE_MAX_CHARS) -> str:
    phrase = re.sub(r"\s+", " ", str(value or "").strip())
    phrase = re.sub(r"^[，。！？、,.!?:：;；~～（）()\[\]【】「」『』“”\"'`]+", "", phrase)
    phrase = re.sub(r"[，。！？、,.!?:：;；~～（）()\[\]【】「」『』“”\"'`]+$", "", phrase)
    if not phrase or "\n" in phrase:
        return ""
    if len(phrase) > max_chars:
        return ""
    normalized = _normalize_alias(phrase)
    if normalized in ASSOCIATIVE_PROMPT_VAGUE_FOCUS:
        return ""
    return phrase


def _role_focus_term(text: str) -> str:
    for pattern in (
        r"(?:当|做|成为|变成|扮成)(?P<focus>[\u4e00-\u9fffA-Za-z0-9_.:-]{2,16})",
    ):
        match = re.search(pattern, str(text or ""))
        if match:
            return _clean_recall_focus(match.group("focus"))
    return ""


def _recall_speaker_pattern(options: MemoryRelevanceOptions) -> str:
    speakers = ["我"]
    speakers.extend(str(term or "").strip() for term in (options.user_terms or ()))
    kept = []
    seen = set()
    for speaker in speakers:
        if not speaker:
            continue
        key = _normalize_alias(speaker)
        if not key or key in seen:
            continue
        seen.add(key)
        kept.append(re.escape(speaker))
    return "|".join(kept) or re.escape("我")


def _safe_float(value: Any) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _compact_emotional_text(value: object) -> str:
    return re.sub(
        r"[\s，。！？、,.!?:：;；~～♡❤♥（）()\[\]【】「」『』“”\"'`-]+",
        "",
        str(value or "").strip().lower(),
    )


def _emotional_term_hits(text: str, terms: frozenset[str]) -> list[tuple[str, int, int]]:
    hits: list[tuple[str, int, int]] = []
    occupied: set[int] = set()
    for term in sorted(terms, key=len, reverse=True):
        needle = _compact_emotional_text(term)
        if not needle:
            continue
        start = 0
        while True:
            index = text.find(needle, start)
            if index < 0:
                break
            end = index + len(needle)
            if not any(position in occupied for position in range(index, end)):
                hits.append((term, index, end))
                occupied.update(range(index, end))
            start = index + 1
    hits.sort(key=lambda item: (item[1], -len(item[0])))
    return hits


def _emotional_compound_terms(
    compact: str,
    emotion: str,
    emotion_start: int,
    emotion_end: int,
    state: str,
    state_start: int,
    state_end: int,
) -> list[str]:
    left = min(emotion_start, state_start)
    right = max(emotion_end, state_end)
    span = compact[left:right]
    state_key = _compact_emotional_text(state)
    terms: list[str] = []
    if state_key in {"哭", "哭了"}:
        terms.append(f"{emotion}哭")
    elif state_key:
        terms.append(f"{emotion}{state_key}")
    if span and span not in terms and len(span) <= 16:
        terms.append(span)
    return terms


def _emotional_query_has_lookup_shell(text: str, compact: str) -> bool:
    return any(term in text or term in compact for term in EMOTIONAL_RECALL_TEMPORAL_TERMS) or any(
        term in text or term in compact for term in EMOTIONAL_RECALL_REASON_TERMS
    )


def _emotional_event_terms(
    query: str,
    options: MemoryRelevanceOptions,
    compact_query: str,
    *,
    max_terms: int,
) -> list[str]:
    output: list[str] = []
    has_lookup_shell = _emotional_query_has_lookup_shell(query, compact_query)
    for term in content_terms_for_query(query, options):
        cleaned = str(term or "").strip()
        key = _compact_emotional_text(cleaned)
        if not key or key in EMOTIONAL_RECALL_EVENT_STOP_TERMS:
            continue
        if _is_context_term(cleaned, options.context_terms):
            continue
        if _is_leading_lookup_address_term(cleaned, compact_query):
            continue
        if any(marker in key for marker in EMOTIONAL_RECALL_REASON_TERMS):
            continue
        if any(marker in key for marker in EMOTIONAL_RECALL_TEMPORAL_TERMS) and len(key) <= 8:
            continue
        if has_lookup_shell and key == compact_query and len(key) > 12:
            continue
        emotion_residue = key
        for emotion in sorted(EMOTIONAL_RECALL_TERMS, key=len, reverse=True):
            emotion_residue = emotion_residue.replace(_compact_emotional_text(emotion), "")
        for state in sorted(EMOTIONAL_RECALL_STATE_TERMS, key=len, reverse=True):
            emotion_residue = emotion_residue.replace(_compact_emotional_text(state), "")
        if not emotion_residue:
            continue
        if emotion_residue != key:
            cleaned = emotion_residue
            key = emotion_residue
        if any(emotion in key for emotion in EMOTIONAL_RECALL_TERMS) and len(key) > 12:
            continue
        if key not in {_compact_emotional_text(item) for item in output}:
            output.append(cleaned)
            if len(output) >= max_terms:
                break
    return output


def _is_leading_lookup_address_term(term: str, compact_query: str) -> bool:
    key = _compact_emotional_text(term)
    if not key or len(key) > 4:
        return False
    index = str(compact_query or "").find(key)
    if index < 0 or index > 1:
        return False
    after = compact_query[index + len(key):]
    lookup_markers = ("知道", "记得", "记不记得", "想起", "想起来", "问", "说")
    return any(after.startswith(marker) for marker in lookup_markers) or any(
        marker in after[:8] for marker in EMOTIONAL_RECALL_REASON_TERMS
    )


def _query_terms(query: str, *, allow_single_cjk: bool = False) -> list[str]:
    raw = str(query or "").strip()
    protected_terms = extract_protected_phrases(raw)
    scan_raw = strip_protected_phrases(raw) if protected_terms else raw
    protected_keys = {_normalize_alias(term) for term in protected_terms if _normalize_alias(term)}
    raw_terms = list(protected_terms)
    raw_terms.extend(part for part in re.split(r"[\s,，。！？!?;；:：/\\|]+", scan_raw) if part)
    raw_terms.extend(jieba.lcut(scan_raw, cut_all=False))
    raw_terms.extend(re.findall(r"[A-Za-z0-9_\-]+|[\u4e00-\u9fff]{2,}", scan_raw))
    terms = []
    for part in raw_terms:
        normalized = _normalize_alias(part)
        if normalized and normalized in protected_keys:
            terms.append(part)
        else:
            terms.extend(_query_term_variants(part))
    kept = []
    seen = set()
    for term in terms:
        normalized = _normalize_alias(term)
        if not normalized or normalized in seen:
            continue
        compact = re.sub(r"[^0-9a-z\u4e00-\u9fff_.:-]+", "", normalized)
        if not compact or compact in QUERY_TERM_STOPWORDS:
            continue
        if not re.search(r"[0-9a-z\u4e00-\u9fff]", compact):
            continue
        if re.fullmatch(r"[a-z0-9_\-]+", normalized) and len(normalized) < 3:
            continue
        if re.fullmatch(r"[\u4e00-\u9fff]+", normalized) and len(normalized) < 2 and not allow_single_cjk:
            continue
        seen.add(normalized)
        kept.append(term)
    return kept


def _query_term_variants(value: str) -> list[str]:
    text = str(value or "").strip()
    if not text:
        return []
    variants = [text]
    normalized = _normalize_alias(text)
    if normalized not in QUERY_WATER_STOPWORDS:
        compact = re.sub(r"[^0-9a-z\u4e00-\u9fff_.:-]+", "", normalized)
        if (
            re.fullmatch(r"([\u4e00-\u9fff])\1", compact)
            and compact[0] not in QUERY_DUPLICATED_CJK_FOLD_DENY_CHARS
        ):
            variants.append(compact[0])
    stripped = re.sub(
        r"(?:期望|希望|想要|需要|应该|不应该)?(?:召回|命中|查到|查一下|找一下|搜到|回忆|记忆)(?:的)?(?:是|到)?",
        " ",
        text,
    )
    stripped = re.sub(r"^(?:的是|是|到)", "", stripped).strip()
    for part in re.split(r"(?:以及|还有|或者|和|与|及|跟|同|、|\+)+", stripped):
        part = re.sub(
            r"^[\s，。！？、,.!?:：;；~～♡❤♥（）()\[\]【】「」『』“”\"'`-]+|[\s，。！？、,.!?:：;；~～♡❤♥（）()\[\]【】「」『』“”\"'`-]+$",
            "",
            part.strip(),
        )
        if part:
            variants.append(part)
    return variants


def _strip_query_water_terms(
    query: str,
    options: MemoryRelevanceOptions | None = None,
    *,
    include_context: bool = True,
) -> str:
    text = str(query or "").strip()
    if not text:
        return ""
    options = options or memory_relevance_options_from_config()
    stop_terms = set(QUERY_TERM_STOPWORDS)
    keep_terms = set(QUERY_KEEPWORDS)
    if include_context:
        stop_terms.update(str(term or "").strip().lower() for term in options.context_terms or ())
    protected_cjk_stop_terms = {
        char
        for term in keep_terms
        if re.fullmatch(r"[\u4e00-\u9fff]+", term)
        for char in term
    }
    stop_terms.difference_update(protected_cjk_stop_terms)
    fragment_stop_terms = set(QUERY_EXTRA_STOPWORDS)
    fragment_stop_terms.difference_update(protected_cjk_stop_terms)
    tokens: list[str] = []
    removed_water = False
    scan_text = text
    phrase_stop_terms = [
        term
        for term in QUERY_EXTRA_STOPWORDS
        if len(term) > 1 and re.fullmatch(r"[\u4e00-\u9fff]+", term)
    ]
    for term in sorted(phrase_stop_terms, key=len, reverse=True):
        if term in scan_text:
            scan_text = scan_text.replace(term, " ")
            removed_water = True
    for part in jieba.lcut(scan_text, cut_all=False):
        for token in re.findall(r"[A-Za-z]+[A-Za-z0-9_.:-]*|\d+(?:\.\d+)+|[\u4e00-\u9fff]+", str(part or "")):
            raw_token = str(token or "").strip()
            normalized = _normalize_alias(raw_token)
            if not normalized:
                continue
            compact_key = re.sub(r"[^0-9a-z\u4e00-\u9fff_.:-]+", "", normalized)
            compact_value = re.sub(r"[^0-9A-Za-z\u4e00-\u9fff_.:-]+", "", raw_token)
            if not compact_key:
                continue
            if compact_key in stop_terms:
                removed_water = True
                continue
            if re.fullmatch(r"[\u4e00-\u9fff]+", compact_key):
                stripped = _strip_cjk_water_fragments(compact_key, fragment_stop_terms)
                if stripped != compact_key:
                    removed_water = True
                compact_key = stripped
                compact_value = stripped
                if not compact_key or compact_key in stop_terms:
                    continue
            if re.fullmatch(r"[a-z0-9_.:-]+", compact_key):
                if re.fullmatch(r"[\d.:-]+", compact_key):
                    continue
            tokens.append(compact_value or compact_key)
    if not removed_water:
        return text
    if not tokens:
        return ""
    if all(re.fullmatch(r"[\u4e00-\u9fff]+", token) for token in tokens):
        return "".join(tokens)
    return " ".join(tokens)


def _strip_cjk_water_fragments(text: str, stop_terms: set[str]) -> str:
    cleaned = str(text or "")
    if not cleaned:
        return ""
    cjk_stop_terms = [
        term
        for term in stop_terms
        if term and re.fullmatch(r"[\u4e00-\u9fff]+", term)
    ]
    for term in sorted(cjk_stop_terms, key=len, reverse=True):
        if term and term != cleaned:
            cleaned = cleaned.replace(term, "")
            if not cleaned:
                return ""
    return cleaned


def _list_text(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [value]
    if isinstance(value, dict):
        return [str(item) for item in value.values() if str(item).strip()]
    if isinstance(value, (list, tuple, set, frozenset)):
        return [str(item) for item in value if str(item).strip()]
    return [str(value)]


def _join_text(value: Any) -> str:
    if isinstance(value, (list, tuple, set, frozenset)):
        return " ".join(str(item) for item in value)
    return str(value or "")


def _normalize_text(text: Any) -> str:
    return re.sub(r"\s+", " ", str(text or "").lower()).strip()


def _normalize_alias(value: Any) -> str:
    return _normalize_text(value)


def _normalize_section(value: Any) -> str:
    return re.sub(r"[\s\-]+", "_", str(value or "").strip().lower())


def _unique(values) -> list[str]:
    seen = set()
    unique = []
    for value in values:
        value = str(value or "").strip()
        if not value or value in seen:
            continue
        seen.add(value)
        unique.append(value)
    return unique
