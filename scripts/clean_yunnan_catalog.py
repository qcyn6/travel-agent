"""在不调用高德的前提下清洗云南景点目录。

命令只读本地 MySQL 景点表、项目已有的云南核验备份和本地知识清单，
不会请求任何地图/POI API，也不会向 attractions 表增加字段。

默认是预览；传入 ``--commit`` 才会更新已有的分类、标签、简介和游览时长
四类规划字段。重复、办事处等高风险记录只输出复核清单，不在本命令中删除。
"""

from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from datetime import datetime
import json
from pathlib import Path
import re
import sys
from typing import Any

from sqlalchemy import select

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from backend.attractions.dedupe import normalize_attraction_name  # noqa: E402
from backend.attractions.metadata import infer_interest_tags  # noqa: E402
from backend.attractions.quality import (  # noqa: E402
    description_origin,
    infer_data_source,
    is_non_attraction_name,
    public_space_kind,
    quality_flags,
)
from backend.database import init_database, open_session  # noqa: E402
from backend.database.tables import AttractionRow, CityRow, ProvinceRow  # noqa: E402
from backend.tag_vocabulary import canonicalize_tags  # noqa: E402
from scripts.enrich_yunnan_local_knowledge import LOCAL_KNOWLEDGE  # noqa: E402


YUNNAN = "云南省"


# 这些是目录中可以仅凭名称确定的分类纠正；不涉及营业时间、票价或坐标。
_CATEGORY_OVERRIDES: dict[str, str] = {
    "B0HKOONUVN": "主题娱乐",  # 小人国主题公园
    "B0FFLIDNEI": "休闲度假",  # 昆明融创滇池后海
    "B0368005Z0": "自然山水",  # 大理苍山世界地质公园
    "B0FFGJWJA3": "休闲度假",  # 理想邦
    "B0FFIPJP95": "主题娱乐",  # 大理海洋世界
    "B0MAMU6I2H": "民俗文化",  # 大理篝火打跳广场
    "B0368000B7": "宗教建筑",  # 小普陀
    "B0368002PY": "宗教建筑",  # 将军洞
    "B036800B74": "历史古迹",  # 南诏德化碑
    "B0LG319HTF": "古镇与街区",  # 珠江源古镇西河坊内的错误“滨海”标签
    "mct-5a-35075": "自然山水",  # 玉龙雪山景区
    "mct-4a-161": "自然山水",  # 观音峡景区
    "mct-4a-2177": "自然山水",  # 蝴蝶泉公园
    "mct-4a-3303": "综合景区",  # 东巴谷旅游景区
    "mct-4a-2349": "民俗文化",  # 玉水寨风景区
    "mct-4a-3062": "自然山水",  # 黎明景区
    "B02B30LWEF": "古镇与街区",  # 告庄西双景
    "mct-4a-1054": "古镇与街区",  # 告庄西双景景区
    "B02B3006LK": "生态动植物",  # 中国科学院西双版纳热带植物园
    "B02B4008ET": "宗教建筑",  # 勐焕大金塔
    "B02B4009OZ": "生态动植物",  # 勐巴娜西珍奇园
    "B0GRL7N28G": "古镇与街区",  # 傣族古镇
    "B0H2L76OAR": "民俗文化",  # 老姆登村景区
    "B03770LV82": "古镇与街区",  # 独克宗古城
    "B0FFM9TXGI": "自然山水",  # 虎跳峡
    "B03770LPV8": "宗教建筑",  # 松赞林景区
    "B03770LV8B": "自然山水",  # 巴拉格宗
    "B036E145MP": "古镇与街区",  # 彝人古镇
    "B036E1464J": "主题娱乐",  # 世界恐龙谷
    "B036E00FI5": "民俗文化",  # 中国彝族十月太阳历文化园
    "B036E0041Y": "古镇与街区",  # 黑井古镇
    "B0373019RH": "古镇与街区",  # 鲁史古镇
    "B036D00DO5": "滨水景观",  # 抚仙湖公园
    "B036D0MLEA": "生态动植物",  # 月亮湾湿地公园
    "B0HA45T638": "滨水景观",  # 红沙滩（抚仙湖岸线）
    "B0FFHU7ULC": "自然山水",  # 普者黑风景区
    "B036C00A0Z": "生态动植物",  # 普者黑喀斯特地貌国家湿地公园
    "B036C0219J": "自然山水",  # 广南坝美风景区
    "B0I6B7OXKV": "古镇与街区",  # 文山七都古镇
    "B0FFGG3R3X": "古镇与街区",  # 建水古城
    "B0FFHCBPBP": "生态动植物",  # 普洱太阳河森林公园
    "B0FFITHK66": "古镇与街区",  # 糯干古寨
    "B0LKYC5681": "历史古迹",  # 普洱茶马古道遗址公园
}


# 只保留明确的公开资料事实，不写营业时间、票价和实时状态。
_PUBLIC_KNOWLEDGE: dict[str, dict[str, Any]] = {
    "B0FFFZH0RQ": {
        "category": "古镇与街区",
        "tags": ("古城", "历史文化", "人文", "白族文化", "建筑", "摄影", "城市漫游"),
        "duration": 240,
        "description": "大理古城位于苍山脚下，是南诏国和大理国历史文化空间的代表。古城街巷、城楼、白族建筑与餐饮生活相互交织，适合步行漫游、建筑观察和城市摄影；规划时应按古城片区安排连续游览，而不是把单个城门当作完整景区。",
    },
    "mct-5a-35076": {
        "category": "古镇与街区",
        "tags": ("古城", "古镇", "历史文化", "纳西文化", "建筑", "摄影", "城市漫游"),
        "duration": 240,
        "description": "丽江古城是滇西北纳西历史文化与传统街巷格局的代表，水系、石桥、木构建筑和四方街共同构成连续的步行体验。适合历史文化、民族风情、摄影和夜间漫游；建议按街巷片区预留半天左右，并以当天人流和活动安排为准。",
    },
    "mct-5a-35075": {
        "category": "自然山水",
        "tags": ("自然", "雪山", "冰雪", "高原", "民族文化", "摄影", "徒步"),
        "duration": 360,
        "description": "玉龙雪山景区以连续雪峰、高原冰川、蓝月谷和纳西族神山文化构成综合自然目的地。景区内不同景点之间存在接驳和海拔变化，适合自然观赏、摄影和轻户外；规划时应为交通、排队、天气和高原适应预留半天以上。",
    },
    "mct-5a-35077": {
        "category": "宗教建筑",
        "tags": ("历史文化", "人文", "宗教文化", "古建", "建筑", "摄影"),
        "duration": 180,
        "description": "崇圣寺三塔文化旅游区以南诏、大理国时期的佛教塔群和寺院遗存为核心，是大理古城西侧的重要历史文化景区。适合古建筑、宗教文化、历史研学和摄影；建议与大理古城安排在同一日，但保留独立的参观时段。",
    },
    "mct-5a-35078": {
        "category": "生态动植物",
        "tags": ("自然", "森林", "园林", "植物", "研学", "亲子", "摄影"),
        "duration": 300,
        "description": "中国科学院西双版纳热带植物园以热带植物收集、科普教育和园林景观为主要内容，是西双版纳适合自然观察与研学的整块目的地。园区面积和步行距离较大，适合亲子、植物爱好者和摄影安排半天以上。",
    },
    "mct-5a-35079": {
        "category": "生态动植物",
        "tags": ("自然", "森林", "高原", "山地", "生态", "摄影", "徒步"),
        "duration": 360,
        "description": "普达措国家公园以高原湖泊、森林、草甸和山地生态系统为主要体验，适合自然观赏、摄影和轻徒步。高原天气与步道状态会影响节奏，规划时应预留半天以上并根据实时开放信息调整线路。",
    },
    "mct-5a-35080": {
        "category": "古典园林",
        "tags": ("园林", "花卉", "休闲", "亲子", "研学", "摄影"),
        "duration": 240,
        "description": "昆明世界园艺博览园以园林展园、植物景观和花卉展示为主要内容，适合城市休闲、亲子、研学和摄影。园区空间较大，建议根据重点展园选择线路并预留半天左右。",
    },
    "mct-5a-35081": {
        "category": "休闲度假",
        "tags": ("自然", "温泉", "地质景观", "休闲", "摄影"),
        "duration": 240,
        "description": "腾冲火山热海旅游区把火山地质景观、地热泉群和温泉休闲结合在一起，适合自然观察、地质研学与放松休闲。火山与热海属于不同体验段，建议按景区线路安排半天，并以当天开放信息为准。",
    },
    "mct-5a-35082": {
        "category": "自然山水",
        "tags": ("自然", "喀斯特", "湖泊", "湿地", "民族文化", "摄影", "乡村"),
        "duration": 300,
        "description": "普者黑旅游景区以喀斯特峰丛、湖泊湿地、村落和彝族文化景观构成区域性自然目的地。水面、陆地步道和村寨体验需要组合安排，适合自然观赏、摄影和乡村慢游，建议预留半天以上。",
    },
    "mct-5a-35083": {
        "category": "古镇与街区",
        "tags": ("古镇", "历史文化", "侨乡", "建筑", "摄影", "城市漫游"),
        "duration": 240,
        "description": "和顺古镇是腾冲侨乡文化、马帮文化与传统民居建筑相互交融的历史村镇，街巷、祠堂、图书馆和水系适合连续步行浏览。适合历史文化、建筑观察和慢游，建议预留半天，并尊重古镇居民的日常生活。",
    },
    "verified-yn-rldjdyj": {
        "category": "滨水景观",
        "tags": ("自然", "江河", "民族文化", "边境风情", "摄影", "自驾"),
        "duration": 240,
        "description": "瑞丽江—大盈江国家级风景名胜区由多个片区和沿江景点组成，兼具河谷、边境口岸和傣族、景颇族等民族文化景观。它是区域性风景廊道，不宜按单一小景点安排；适合沿线观景、自驾和摄影，具体停留点应结合当天线路。",
    },
    "verified-yn-jiegao": {
        "category": "古镇与街区",
        "tags": ("边境风情", "跨境文化", "商业街", "民族文化", "美食", "城市漫游"),
        "duration": 120,
        "description": "姐告边境贸易区是瑞丽面向缅甸的开放式边境旅游空间，兼具口岸、边贸、珠宝玉石和民族风情体验。它适合安排半日城市漫游和边境文化观察；通行、口岸管理和开放范围以出行当天的官方信息为准。",
    },
    "verified-yn-nujiang-canyon": {
        "category": "自然山水",
        "tags": ("自然", "峡谷", "江河", "山地", "民族文化", "摄影", "自驾"),
        "duration": 300,
        "description": "怒江大峡谷是由江河深切峡谷、碧罗雪山与高黎贡山山地、村落和多民族文化共同构成的连续旅行廊道。它不适合按一个城市景点计算时长，规划时应按县市、观景台和村寨拆分，并为山路和天气留出弹性。",
    },
    "verified-yn-dulongjiang": {
        "category": "自然山水",
        "tags": ("自然", "峡谷", "江河", "民族文化", "徒步", "摄影", "乡村"),
        "duration": 300,
        "description": "独龙江生态体验区依托独龙江乡的峡谷河流、森林、村落和独龙族文化，适合生态观察、徒步和民族文化体验。它是区域性目的地，交通与季节性天气会显著影响节奏，规划时应按完整线路预留时间。",
    },
    "demo-533300-05": {
        "category": "民俗文化",
        "tags": ("民族文化", "民俗", "乡村", "山地", "摄影", "休闲"),
        "duration": 240,
        "description": "老姆登村位于碧罗雪山半山腰，是具有浓郁怒族风情的特色村寨，老姆登教堂、民族歌舞、火塘和山地峡谷视野共同构成主要体验。适合乡村慢游、民族文化和摄影，建议与知子罗、沙瓦等周边点位组合安排。",
    },
}


# 第二轮公开网页核验清单。这里仅收录能够在政府、景区官网、文旅部门、
# 科研机构或世界遗产等公开页面中交叉确认的记录；不写营业时间、票价、
# 实时开放状态，也不新增数据库字段。名称过于泛化且无法找到景点级资料的
# 记录由 scripts/prune_yunnan_unverified.py 另行审计和删除。
_DERIVED_PUBLIC_KNOWLEDGE: dict[str, dict[str, Any]] = {
    "B02B4008ET": {
        "category": "宗教建筑",
        "tags": ("宗教文化", "建筑", "民族文化", "摄影", "人文"),
        "duration": 90,
        "description": "勐焕大金塔是芒市具有代表性的傣族佛教建筑和城市地标，金色塔体、佛教空间与傣族文化共同构成主要体验。适合宗教文化、建筑观察和摄影，建议结合芒市城区其他文化点位安排。",
    },
    "B02B4007OF": {
        "category": "民俗文化",
        "tags": ("边境风情", "民族文化", "乡村", "摄影", "人文"),
        "duration": 120,
        "description": "一寨两国位于瑞丽边境，村寨空间跨越中缅边界，边境标志、村落生活和傣族文化构成主要看点。适合边境风情、民族文化和摄影体验，通行与开放范围以当天官方信息为准。",
    },
    "B02B4009OZ": {
        "category": "生态动植物",
        "tags": ("森林", "植物", "园林", "研学", "摄影", "民族文化"),
        "duration": 180,
        "description": "勐巴娜西珍奇园是芒市的热带植物与园林型景区，集中展示古树名木、珍稀植物、奇石和根雕等内容。适合植物观察、生态研学、园林漫游和摄影，通常可安排三小时左右。",
    },
    "B0376001H0": {
        "category": "自然山水",
        "tags": ("峡谷", "江河", "山地", "民族文化", "摄影", "自驾"),
        "duration": 300,
        "description": "丙中洛风景区位于怒江上游峡谷地带，江河、雪山、村落和多民族文化共同构成连续的山地景观。适合自然观赏、摄影和自驾线路，景点之间距离较长，建议按区域预留半天以上。",
    },
    "B0376001N6": {
        "category": "自然山水",
        "tags": ("山地", "自然", "摄影", "自驾", "江河"),
        "duration": 60,
        "description": "石月亮观景台面向怒江峡谷中的天然石月亮地貌，是观察山地河谷和拍摄自然景观的路线上停靠点。适合短暂停留、观景和摄影，实际可见度受天气与山路状况影响。",
    },
    "B0FFHU7ULC": {
        "category": "自然山水",
        "tags": ("自然", "喀斯特", "湖泊", "湿地", "民族文化", "摄影", "乡村"),
        "duration": 300,
        "description": "普者黑风景区以喀斯特峰丛、湖泊湿地、村落和彝族文化景观构成区域性自然目的地。水面、陆地步道和村寨体验需要组合安排，适合自然观赏、摄影和乡村慢游，建议预留半天以上。",
    },
    "B036C00A0Z": {
        "category": "生态动植物",
        "tags": ("喀斯特", "湿地", "湖泊", "自然", "研学", "摄影"),
        "duration": 180,
        "description": "云南普者黑喀斯特地貌国家湿地公园以喀斯特峰丛、湖泊、湿地和水生生态系统为主要内容，是普者黑片区适合自然观察与科普的空间。适合湿地观赏、摄影和轻量研学，建议结合普者黑主景区安排。",
    },
    "B0FFG6QV3L": {
        "category": "城市公园",
        "tags": ("城市漫游", "人文", "民族文化", "休闲", "摄影"),
        "duration": 60,
        "description": "文山七花广场是文山市区的公共文化休闲空间，承担城市活动、群众休闲和地方文化展示等功能。适合城市漫游、短时休息和观察地方公共生活，不宜按大型自然景区安排时长。",
    },
    "B036C0219J": {
        "category": "自然山水",
        "tags": ("喀斯特", "溶洞", "乡村", "民族文化", "摄影", "自然"),
        "duration": 240,
        "description": "广南坝美风景区以喀斯特山水、溶洞通道、河流村落和壮族村寨文化为主要体验，适合自然观赏、乡村慢游和民族文化体验。水上与步行段需要组合安排，建议预留半天左右。",
    },
    "B036C0NVHC": {
        "category": "城市公园",
        "tags": ("城市漫游", "人文", "休闲", "民族文化"),
        "duration": 45,
        "description": "七都广场是文山七都古镇及相关城市文化项目中的公共广场空间，主要承担休闲、集会和地方文化展示功能。适合短时停留和城市漫游，规划时应与古镇片区合并计算。",
    },
    "B036C0OEJ6": {
        "category": "自然山水",
        "tags": ("溶洞", "湖泊", "喀斯特", "自然", "摄影", "乡村"),
        "duration": 120,
        "description": "仙人洞位于普者黑喀斯特湖区及村落景观带，洞穴、湖面和周边村寨共同构成游览内容。适合自然观察、摄影和短线乡村游，实际停留时间取决于水路与步道开放情况。",
    },
    "B036C008PT": {
        "category": "自然山水",
        "tags": ("山地", "红色文化", "历史文化", "自然", "摄影", "徒步"),
        "duration": 240,
        "description": "老山风景名胜区以边境山地自然环境和老山红色历史文化为核心，兼具山林景观、纪念设施和爱国主义教育内容。适合红色文化、山地观景与徒步线路，建议按开放片区预留半天。",
    },
    "B0FFKUWDPH": {
        "category": "历史古迹",
        "tags": ("建筑", "摄影", "湖泊", "乡村", "自然"),
        "duration": 60,
        "description": "情人桥是普者黑湖区步行与观景线路中的桥梁节点，连接水面、峰丛和村落景观，适合短暂停留和摄影。它属于区域景区中的组合点位，建议与湖区游船或步道一起规划。",
    },
    "B0I6B7OXKV": {
        "category": "古镇与街区",
        "tags": ("古镇", "历史文化", "民族文化", "建筑", "城市漫游", "美食"),
        "duration": 150,
        "description": "文山七都古镇是以地方历史、民族文化和传统街区体验为主题的城市文旅片区，适合步行浏览街巷、建筑与公共文化空间。建议与七都广场等周边点位组合安排，并按实际开放业态调整时长。",
    },
    "B03730MTQG": {
        "category": "自然山水",
        "tags": ("乡村", "民族文化", "民俗", "建筑", "摄影", "历史文化"),
        "duration": 180,
        "description": "翁丁原始部落是沧源佤族村寨型景区，传统村落、佤族建筑、民俗活动和山地环境构成主要体验。适合民族文化、乡村慢游与摄影，建议尊重村民生活并按景区开放安排参观。",
    },
    "B0FFF40KXP": {
        "category": "民俗文化",
        "tags": ("民族文化", "民俗", "乡村", "美食", "摄影", "休闲"),
        "duration": 150,
        "description": "葫芦小镇以佤族文化、村镇生活和慢旅游体验为主题，结合民俗展示、特色建筑与休闲业态。适合民族文化、乡村漫游和亲子休闲，建议预留两至三小时并以现场活动为准。",
    },
    "B0FFFOGF6A": {
        "category": "滨水景观",
        "tags": ("湖泊", "湿地", "休闲", "摄影", "城市漫游"),
        "duration": 90,
        "description": "玉龙湖是临沧城区重要的湖滨生态公园，湖面、桥梁、绿地和步行空间组成城市休闲景观。适合散步、短时休息和城市摄影，建议按城市公园节奏安排一至两小时。",
    },
    "B0373019RH": {
        "category": "古镇与街区",
        "tags": ("古镇", "历史文化", "茶文化", "建筑", "摄影", "城市漫游"),
        "duration": 180,
        "description": "鲁史古镇是茶马古道沿线的历史村镇，传统街巷、会馆民居和马帮贸易记忆构成主要看点。适合历史文化、建筑观察和古镇慢游，建议预留半天左右。",
    },
    "B037300471": {
        "category": "城市公园",
        "tags": ("城市漫游", "休闲", "园林", "摄影"),
        "duration": 60,
        "description": "西门公园是临沧城区的城市公园，提供绿地、步行和公共休闲空间。适合城市漫游、短时休息和观察当地日常生活，不宜与大型景区等量安排。",
    },
    "B0L33CAA5A": {
        "category": "乡村旅游",
        "tags": ("茶文化", "乡村", "山地", "摄影", "自然"),
        "duration": 150,
        "description": "冰岛老寨是临沧双江县的重要普洱茶产地村寨，古茶园、山地村落和茶文化体验构成主要内容。适合茶文化、乡村漫游和摄影，具体采茶及参访安排应以当地接待条件为准。",
    },
    "B0FFGFV66V": {
        "category": "历史古迹",
        "tags": ("建筑", "历史文化", "城市漫游", "摄影", "人文"),
        "duration": 60,
        "description": "立景阁是临沧城区的历史文化景观与城市观景节点，承担传统建筑展示、城市眺望和公共文化活动等功能。适合短时参观、建筑观察和城市摄影，建议与城区其他点位组合安排。",
    },
    "B0FFLCSQ1B": {
        "category": "自然山水",
        "tags": ("茶文化", "森林", "乡村", "民族文化", "世界遗产", "摄影"),
        "duration": 300,
        "description": "普洱景迈山茶林文化景区以古茶林、传统村落和布朗族、傣族等茶文化共同体为核心，体现茶林共生的山地景观。适合世界遗产、茶文化、民族文化和乡村慢游，建议按村落与茶林线路安排半天以上。",
    },
    "B0FFHCBPBP": {
        "category": "生态动植物",
        "tags": ("森林", "自然", "生态", "野生动物", "研学", "摄影"),
        "duration": 300,
        "description": "普洱太阳河森林公园以热带山地森林、野生动物和生态科普为主要体验，是适合自然观察与研学的森林型景区。园区范围较大，建议预留半天以上，并按开放步道和接驳安排线路。",
    },
    "B036F0060T": {
        "category": "历史古迹",
        "tags": ("茶文化", "古镇", "历史文化", "建筑", "美食", "摄影"),
        "duration": 120,
        "description": "那柯里茶马驿站是普洱茶马古道沿线的历史驿站型文旅点，传统民居、马帮记忆和茶文化体验构成主要内容。适合茶文化、历史建筑和短线慢游，通常可与宁洱周边线路组合。",
    },
    "B0FFITHK66": {
        "category": "古镇与街区",
        "tags": ("古村", "古镇", "茶文化", "世界遗产", "民族文化", "摄影"),
        "duration": 150,
        "description": "糯干古寨是景迈山古茶林文化景观中的传统村寨，傣族民居、古茶林和山地聚落共同构成游览内容。适合世界遗产、茶文化、民族建筑和乡村慢游，应注意村寨生活秩序。",
    },
    "B036F0043N": {
        "category": "自然山水",
        "tags": ("茶文化", "森林", "世界遗产", "乡村", "自然", "摄影"),
        "duration": 240,
        "description": "景迈千年万亩古茶园是景迈山茶林文化景观的重要组成部分，古茶树、森林和山地村落体现长期形成的茶林共生关系。适合茶文化、生态观察和摄影，建议结合景迈山村寨线路安排。",
    },
    "B0KGOSFOJ5": {
        "category": "自然山水",
        "tags": ("茶文化", "山地", "森林", "世界遗产", "摄影"),
        "duration": 60,
        "description": "景迈山观景台是观察古茶林、山地村落和茶林景观整体格局的视点，适合短时停留、拍摄和线路休息。它属于景迈山区域线路中的观景节点，建议与古茶园、村寨一起规划。",
    },
    "B0LKYC5681": {
        "category": "历史古迹",
        "tags": ("茶文化", "历史文化", "古建", "乡村", "摄影"),
        "duration": 120,
        "description": "普洱茶马古道遗址公园以茶马古道交通遗迹、驿站记忆和茶文化展示为主要内容，适合历史研学与茶文化体验。建议与那柯里等沿线点位组合安排，并按开放展陈内容调整时长。",
    },
    "B0FFF35SIE": {
        "category": "自然山水",
        "tags": ("湖泊", "自然", "休闲", "摄影", "乡村"),
        "duration": 120,
        "description": "明星渔洞位于抚仙湖沿岸，以湖泊水域、渔业文化和滨水休闲为主要体验，适合观湖、摄影和短线亲水活动。具体水上项目与开放范围以现场管理信息为准。",
    },
    "B036D0MLEA": {
        "category": "生态动植物",
        "tags": ("湿地", "湖泊", "自然", "亲子", "摄影", "休闲"),
        "duration": 120,
        "description": "月亮湾湿地公园是抚仙湖东岸的湖滨湿地与休闲空间，兼具水岸景观、湿地生态和步行观景功能。适合亲子、散步和摄影，建议按滨湖公园节奏安排一至两小时。",
    },
    "B036D00VD3": {
        "category": "休闲度假",
        "tags": ("湖泊", "休闲", "亲子", "摄影", "体育"),
        "duration": 120,
        "description": "映月潭休闲文化中心是玉溪的滨水休闲与体育文化空间，围绕湖滨环境开展休闲、运动和群众文化活动。适合轻量休闲、亲子和城市周边短途游，具体活动以现场安排为准。",
    },
    "B0HA45T638": {
        "category": "滨水景观",
        "tags": ("湖泊", "湿地", "摄影", "自然", "休闲"),
        "duration": 90,
        "description": "红沙滩是抚仙湖岸线上的滨水观景点，湖岸、浅滩和季节性水色构成主要景观，适合观湖和摄影。它属于湖区组合点位，建议与月亮湾、明星渔洞等岸线景观连线安排。",
    },
    "B036E145MP": {
        "category": "古镇与街区",
        "tags": ("古镇", "民族文化", "历史文化", "建筑", "美食", "夜游"),
        "duration": 180,
        "description": "彝人古镇以彝族建筑、民俗展示、街区商业和夜间文化活动为主要内容，是楚雄城市文旅体验的重要片区。适合民族文化、建筑观察、美食与夜游，建议按街区活动安排两至三小时。",
    },
    "B036E1464J": {
        "category": "主题娱乐",
        "tags": ("主题娱乐", "化石", "研学", "亲子", "地质景观", "自然"),
        "duration": 300,
        "description": "世界恐龙谷以恐龙化石遗址、科普展示、主题游乐和温泉休闲为主要内容，兼具地质研学与亲子娱乐属性。园区内容较多，建议预留半天以上，并按开放项目安排线路。",
    },
    "B036E00E0L": {
        "category": "自然山水",
        "tags": ("地质景观", "丹霞", "自然", "摄影", "徒步", "研学"),
        "duration": 180,
        "description": "元谋物茂土林风景区以风蚀和流水作用形成的土林地貌为核心，土柱群、峡谷和高原河谷环境构成独特地质景观。适合地质研学、摄影和轻徒步，建议避开高温时段并预留三小时左右。",
    },
    "B036E00FI5": {
        "category": "民俗文化",
        "tags": ("民族文化", "历史文化", "研学", "建筑", "摄影", "人文"),
        "duration": 120,
        "description": "中国彝族十月太阳历文化园以彝族历法、天文观念和民族文化展示为主题，适合文化研学、建筑观察和民族风情体验。建议结合楚雄彝族文化线路安排一至两小时。",
    },
    "B036E0041Y": {
        "category": "古镇与街区",
        "tags": ("古镇", "历史文化", "盐文化", "建筑", "摄影", "乡村"),
        "duration": 180,
        "description": "黑井古镇因盐业贸易兴起，保存有传统街巷、民居、会馆和盐业历史遗存，是滇中历史村镇的代表。适合历史文化、建筑观察和古镇慢游，建议预留半天左右。",
    },
    "B0GRL7N28G": {
        "category": "古镇与街区",
        "tags": ("古镇", "民族文化", "建筑", "美食", "夜游", "城市漫游"),
        "duration": 150,
        "description": "傣族古镇是芒市以傣族建筑、民族文化展示、餐饮和夜间休闲为主题的城市文旅街区。适合民族风情、建筑观察、美食和夜游，建议根据演艺与商业开放情况安排。",
    },
    "B02B4008EF": {
        "category": "城市公园",
        "tags": ("城市漫游", "休闲", "民族文化", "摄影", "人文"),
        "duration": 60,
        "description": "芒市广场是芒市中心的城市公共空间，承担节庆活动、群众休闲和地方文化展示等功能。适合城市漫游和短时停留，规划时应与城区寺塔、街区点位组合。",
    },
    "B02B40MF07": {
        "category": "历史古迹",
        "tags": ("历史文化", "宗教文化", "建筑", "摄影", "人文"),
        "duration": 60,
        "description": "树包塔是芒市具有地方辨识度的历史文化景观，古树与佛塔相互包裹的形态构成主要看点。适合短时参观、建筑与城市历史观察，建议与芒市城区其他点位连线。",
    },
    "B0FFF4EEBL": {
        "category": "城市公园",
        "tags": ("湖泊", "湿地", "城市漫游", "休闲", "摄影"),
        "duration": 90,
        "description": "弄莫湖公园是瑞丽城区的湖滨生态公园，湖面、绿地和步行空间构成城市休闲景观。适合散步、短时休息和摄影，建议按城市公园节奏安排一至两小时。",
    },
    "B02B4008EV": {
        "category": "休闲度假",
        "tags": ("湖泊", "休闲", "民族文化", "自然", "摄影"),
        "duration": 150,
        "description": "芒市孔雀湖旅游度假区以人工湖滨水景观、休闲度假空间和傣族文化资源为主要内容，连接金塔、银塔、勐巴娜西和傣族古镇等城市文旅点位。适合滨水休闲与城市度假，建议按片区组合规划。",
    },
    "B03770LV82": {
        "category": "古镇与街区",
        "tags": ("古城", "古镇", "藏族文化", "历史文化", "建筑", "摄影"),
        "duration": 180,
        "description": "香格里拉市独克宗古城是滇西北藏族历史文化街区，传统民居、经幡、寺院与高原街巷共同构成步行体验。适合藏族文化、建筑观察、摄影和夜间漫游，建议预留半天左右。",
    },
    "B0FFM9TXGI": {
        "category": "自然山水",
        "tags": ("峡谷", "江河", "山地", "自然", "摄影", "徒步"),
        "duration": 300,
        "description": "香格里拉虎跳峡景区以金沙江深切峡谷、巨石激流和高山地貌为主要景观，适合自然观赏、摄影和分段徒步。峡谷高差与步道强度较大，规划时应根据线路和体力预留半天以上。",
    },
    "B03770LPV8": {
        "category": "宗教建筑",
        "tags": ("宗教文化", "藏族文化", "建筑", "历史文化", "摄影"),
        "duration": 150,
        "description": "松赞林景区以藏传佛教寺院建筑群、壁画造像和高原宗教文化为核心，是云南重要的藏传佛教文化景观。适合宗教文化、建筑观察与摄影，参观时应遵守寺院礼仪。",
    },
    "B03770LV8B": {
        "category": "自然山水",
        "tags": ("峡谷", "高原", "山地", "自然", "摄影", "徒步"),
        "duration": 300,
        "description": "香格里拉大峡谷·巴拉格宗国家级风景名胜区以高原峡谷、雪山、河流和藏族村落为主要景观，兼具自然观赏与民族文化体验。景区线路较长，适合摄影和轻户外，建议预留半天以上。",
    },
    "B02B30LWEF": {
        "category": "古镇与街区",
        "tags": ("民族文化", "夜游", "美食", "建筑", "摄影", "城市漫游"),
        "duration": 180,
        "description": "告庄西双景是景洪澜沧江畔的傣族文化与夜间休闲街区，星光夜市、滨江空间、建筑景观和餐饮商业共同构成体验。适合民族风情、美食、摄影和夜游，建议按晚间人流安排。",
    },
    "B02B300DRK": {
        "category": "古典园林",
        "tags": ("园林", "民族文化", "历史文化", "建筑", "摄影", "花卉"),
        "duration": 180,
        "description": "曼听御花园景区以傣王御花园遗存、热带园林、佛寺建筑和民族文化演艺为主要内容，适合园林漫游、历史文化和摄影。园区与傣族文化展示结合紧密，建议预留两至三小时。",
    },
    "B02B3006LK": {
        "category": "生态动植物",
        "tags": ("植物", "森林", "热带", "研学", "摄影", "自然"),
        "duration": 360,
        "description": "中国科学院西双版纳热带植物园集热带植物保存、科学研究、科普教育和生态旅游于一体，园区植物专类园和热带雨林景观适合自然观察与研学。步行范围较大，建议预留半天以上。",
    },
    "B0FFGG3R3X": {
        "category": "古镇与街区",
        "tags": ("古城", "古镇", "历史文化", "建筑", "摄影", "城市漫游"),
        "duration": 240,
        "description": "建水古城保存有传统城门、街巷、民居、文庙和朱家花园等历史文化资源，是滇南古城步行体验的代表。适合历史文化、古建筑、美食和城市漫游，建议预留半天左右。",
    },
    "B0H2L76OAR": {
        "category": "民俗文化",
        "tags": ("民族文化", "民俗", "乡村", "山地", "摄影", "历史文化"),
        "duration": 180,
        "description": "老姆登村是怒族特色村寨，山地聚落、老姆登教堂、民族歌舞和峡谷视野共同构成主要体验。适合民族文化、乡村慢游和摄影，建议与知子罗等周边点位组合安排。",
    },
    "B0376002IP": {
        "category": "滨水景观",
        "tags": ("江河", "峡谷", "自然", "摄影", "自驾", "山地"),
        "duration": 90,
        "description": "怒江第一湾是怒江峡谷中江流形成的大尺度回转河湾景观，观景台、河谷山体和道路共同构成主要体验。适合短时观景、摄影和自驾线路停靠，天气与道路状况会影响可见度。",
    },
    "B0J0XMH680": {
        "category": "自然山水",
        "tags": ("山地", "边境风情", "自然", "摄影", "自驾"),
        "duration": 60,
        "description": "滇藏界是滇藏公路沿线的省域交界与高山景观节点，山地道路、峡谷和边境地理环境构成主要看点。适合自驾途中短暂停留与摄影，不宜按独立大型景区计算时长。",
    },
    "B03760HTEW": {
        "category": "自然山水",
        "tags": ("峡谷", "江河", "山地", "自然", "摄影", "自驾"),
        "duration": 60,
        "description": "丙中洛观景台是观察怒江河谷、村落和山地景观的视点，适合短时停留、路线休息和摄影。它属于丙中洛区域景区的组合节点，建议与怒江第一湾、秋那桶等线路共同安排。",
    },
    "B02B4008TM": {
        "category": "自然山水",
        "tags": ("热带雨林", "森林", "瀑布", "自然", "徒步", "摄影"),
        "duration": 180,
        "description": "莫里热带雨林风景区以热带雨林、溪流瀑布和峡谷步道为主要景观，是瑞丽边境生态旅游的重要片区。适合森林观察、轻徒步和摄影，建议按步道开放情况预留三小时左右。",
    },
    "B02B4008ES": {
        "category": "宗教建筑",
        "tags": ("宗教文化", "建筑", "历史文化", "摄影", "人文"),
        "duration": 60,
        "description": "菩提寺是芒市城区的佛教寺院与历史文化建筑，寺院空间、宗教活动和傣族地域文化构成主要体验。适合建筑观察、宗教文化和安静参观，进入寺院应遵守相关礼仪。",
    },
    "B0FFKXA31J": {
        "category": "历史古迹",
        "tags": ("峡谷", "江河", "自然", "摄影", "山地"),
        "duration": 60,
        "description": "老虎跳是怒江峡谷沿线的河流与山地景观节点，激流、巨石和峡谷地貌构成主要看点。适合短时观景和摄影，属于自驾线路上的组合停靠点，需注意道路与安全边界。",
    },
    "B0FFF364S9": {
        "category": "自然山水",
        "tags": ("山地", "自然", "峡谷", "摄影", "徒步"),
        "duration": 90,
        "description": "贡当神山是怒江峡谷沿线的山地自然景观节点，山体、峡谷和高原村落视野构成主要体验。适合观景、摄影和轻户外，建议结合丙中洛区域道路与天气安排。",
    },
    "B0H2L76OAQ": {
        "category": "历史古迹",
        "tags": ("乡村", "民族文化", "山地", "摄影", "自然"),
        "duration": 180,
        "description": "秋那桶村景区位于怒江上游峡谷地带，传统村落、山地环境和多民族文化共同构成体验。适合乡村慢游、摄影和自然观察，交通距离较长，建议与丙中洛线路整体安排。",
    },
    "B0G0DS8H7L": {
        "category": "滨水景观",
        "tags": ("湖泊", "乡村", "历史文化", "休闲", "摄影"),
        "duration": 120,
        "description": "昆明卧龙古渔村位于滇池湖岸，以传统渔村空间、村落建筑和湖滨景观为主要体验。适合乡村漫游、湖畔摄影和地方文化观察，属于滇池滨水目的地，不属于海滨或海岛景点。",
    },
    "B0G2AK3KPJ": {
        "category": "滨水景观",
        "tags": ("湖泊", "乡村", "历史文化", "休闲", "摄影"),
        "duration": 120,
        "description": "海晏古渔村位于昆明滇池东岸，保留传统村落空间、古渔村生活记忆和湖滨日落景观。适合村落漫游、湖景摄影和近郊休闲，分类按滇池滨水景观处理。",
    },
}


_VERIFIED_PUBLIC_KNOWLEDGE = {**_DERIVED_PUBLIC_KNOWLEDGE, **_PUBLIC_KNOWLEDGE}


# 仅用于审计报告，不会写入 attractions 表。名称来自当前云南目录中已确认
# 的同景区、父子景点或行政办事处记录。
_OVERLAP_GROUPS: tuple[tuple[str, ...], ...] = (
    ("石林风景区", "昆明市石林风景名胜区"),
    ("世界园艺博览园", "昆明市昆明世界园艺博览园景区"),
    ("官渡古镇", "云南省昆明市官渡区官渡古镇"),
    ("九乡风景区", "云南省昆明市宜良县九乡风景区"),
    ("西山风景区", "云南省昆明市西山区西山风景区"),
    ("丽江古城", "丽江市丽江古城景区"),
    ("玉龙雪山国家级风景名胜区", "丽江市玉龙雪山景区"),
    ("束河古镇", "云南省丽江市古城区束河古镇景区"),
    ("泸沽湖", "云南省丽江市宁蒗彝族自治县泸沽湖景区"),
    ("东巴谷景区", "云南省丽江市玉龙纳西族自治县东巴谷旅游景区"),
    ("大理古城", "云南省大理白族自治州大理市大理古城景区"),
    ("崇圣寺三塔文化旅游区", "大理州崇圣寺三塔文化旅游区"),
    ("南诏风情岛", "云南省大理白族自治州大理市南诏风情岛景区"),
    ("蝴蝶泉", "云南省大理白族自治州大理市喜洲镇蝴蝶泉公园"),
    ("普者黑风景区", "文山州普者黑旅游景区"),
    ("中国科学院西双版纳热带植物园", "西双版纳州中国科学院西双版纳热带植物园景区"),
    ("告庄西双景", "云南省西双版纳傣族自治州景洪市告庄西双景景区"),
    ("恐龙谷", "云南省楚雄彝族自治州禄丰县恐龙谷景区"),
    ("老姆登村", "老姆登村景区"),
    ("丽江虎跳峡景区", "云南省迪庆藏族自治州香格里拉市虎跳峡景区"),
    ("香格里拉虎跳峡景区", "云南省迪庆藏族自治州香格里拉市虎跳峡景区"),
)


def _backup_path(directory: Path) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    path = directory / f"yunnan-catalog-before-clean-{stamp}.json"
    suffix = 1
    while path.exists():
        path = directory / f"yunnan-catalog-before-clean-{stamp}-{suffix}.json"
        suffix += 1
    return path


def _load_verified_reference(path: Path) -> dict[str, dict[str, Any]]:
    """读取旧的云南公开资料快照；快照缺失时仍可执行本地规则。"""

    if not path.exists():
        return {}
    payload = json.loads(path.read_text(encoding="utf-8"))
    result: dict[str, dict[str, Any]] = {}
    for row in payload.get("rows", []):
        if not row.get("poi_id"):
            continue
        # 这个快照只用于规划字段；坐标和城市归属留在当前数据库中。
        result[str(row["poi_id"])] = {
            "category": row.get("category"),
            "tags": tuple(row.get("tags") or ()),
            "description": row.get("description") or "",
            "duration": row.get("visit_duration_minutes"),
        }
    return result


def _knowledge_by_poi() -> dict[str, dict[str, Any]]:
    return {
        item.poi_id: {
            "category": item.category,
            "tags": item.tags,
            "description": item.description,
            "duration": item.visit_duration_minutes,
        }
        for item in LOCAL_KNOWLEDGE
    }


def _payload(row: AttractionRow) -> dict[str, Any]:
    return {column.name: getattr(row, column.name) for column in row.__table__.columns}


def _row_mapping(row: AttractionRow) -> dict[str, Any]:
    return {
        "id": row.id,
        "poi_id": row.poi_id,
        "name": row.name,
        "category": row.category,
        "tags": list(row.tags or []),
        "description": row.description,
        "longitude": row.longitude,
        "latitude": row.latitude,
        "visit_duration_minutes": row.visit_duration_minutes,
    }


def _normalise_tags(values: Any) -> list[str]:
    return canonicalize_tags(values or (), fallback="人文")


def _overlap_candidates(rows: list[AttractionRow]) -> list[list[dict[str, Any]]]:
    by_normalised = defaultdict(list)
    for row in rows:
        by_normalised[normalize_attraction_name(row.name)].append(row)
    result: list[list[dict[str, Any]]] = []
    for aliases in _OVERLAP_GROUPS:
        selected: list[AttractionRow] = []
        alias_keys = {normalize_attraction_name(name) for name in aliases}
        for key in alias_keys:
            selected.extend(by_normalised.get(key, []))
        # Prefixes such as “云南省大理州……” make exact matching too strict.
        if not selected:
            for row in rows:
                value = normalize_attraction_name(row.name)
                if any(normalize_attraction_name(alias) in value or value in normalize_attraction_name(alias) for alias in aliases):
                    selected.append(row)
        unique = {row.id: row for row in selected}
        if len(unique) > 1:
            result.append([
                {
                    "id": row.id,
                    "poi_id": row.poi_id,
                    "name": row.name,
                    "category": row.category,
                    "source": infer_data_source(row.poi_id),
                }
                for row in sorted(unique.values(), key=lambda item: item.id)
            ])
    return result


def _noise_candidates(rows: list[AttractionRow]) -> list[dict[str, Any]]:
    result = []
    for row in rows:
        flags = quality_flags(_row_mapping(row))
        if is_non_attraction_name(row.name) or "non_attraction_name" in flags:
            result.append({
                "id": row.id,
                "poi_id": row.poi_id,
                "name": row.name,
                "category": row.category,
                "reason": "非景点服务/办事处名称",
            })
    return result


def _public_space_candidates(rows: list[AttractionRow]) -> list[dict[str, Any]]:
    result = []
    for row in rows:
        kind = public_space_kind(row.name, row.category)
        if kind in {"urban_park", "urban_square", "facility_square"}:
            result.append({
                "id": row.id,
                "poi_id": row.poi_id,
                "name": row.name,
                "category": row.category,
                "kind": kind,
            })
    return result


def clean(
    *,
    commit: bool,
    backup_dir: Path,
    reference_path: Path,
) -> dict[str, Any]:
    init_database()
    reference = _load_verified_reference(reference_path)
    local = _knowledge_by_poi()
    with open_session() as session:
        pairs = list(
            session.execute(
                select(AttractionRow, CityRow)
                .join(CityRow, CityRow.id == AttractionRow.city_id)
                .join(ProvinceRow, ProvinceRow.id == CityRow.province_id)
                .where(ProvinceRow.name == YUNNAN)
                .order_by(AttractionRow.id)
            ).all()
        )
        rows = [row for row, _city in pairs]
        changes: list[dict[str, Any]] = []
        before_rows: dict[int, dict[str, Any]] = {}
        counters = Counter()
        reference_rows = 0
        local_rows = 0
        public_rows = 0
        post_descriptions: dict[int, str] = {}

        for row, city in pairs:
            # Capture the complete row before mutating the ORM object.  The
            # rollback snapshot must represent the actual pre-clean state,
            # including coordinates and every column currently in the schema.
            before_rows[row.id] = _payload(row)
            before = {
                "category": row.category,
                "tags": list(row.tags or []),
                "description": row.description,
                "visit_duration_minutes": row.visit_duration_minutes,
            }
            update: dict[str, Any] = {}

            if row.poi_id in reference:
                update.update(reference[row.poi_id])
                reference_rows += 1
                counters["reference"] += 1
            if row.poi_id in local:
                update.update(local[row.poi_id])
                local_rows += 1
                counters["local_knowledge"] += 1
            if row.poi_id in _VERIFIED_PUBLIC_KNOWLEDGE:
                update.update(_VERIFIED_PUBLIC_KNOWLEDGE[row.poi_id])
                public_rows += 1
                counters["public_knowledge"] += 1

            category = _CATEGORY_OVERRIDES.get(row.poi_id) or update.get("category") or row.category
            tags = _normalise_tags(update.get("tags") if "tags" in update else row.tags)
            if row.poi_id in _CATEGORY_OVERRIDES and row.poi_id not in local and row.poi_id not in _VERIFIED_PUBLIC_KNOWLEDGE:
                tags = _normalise_tags(infer_interest_tags(row.name, category))
            description = update.get("description") or row.description
            duration = update.get("duration") or row.visit_duration_minutes

            # 公开资料快照/本地知识中的描述优先；其余 derived 描述暂不强行
            # 改写，避免再次生成没有事实依据的“真实简介”。
            after = {
                "category": category,
                "tags": tags,
                "description": description,
                "visit_duration_minutes": int(duration or 90),
            }
            post_descriptions[row.id] = after["description"]
            if before != after:
                changes.append({
                    "id": row.id,
                    "poi_id": row.poi_id,
                    "city": city.name,
                    "name": row.name,
                    "before": before,
                    "after": after,
                })
                for field in before:
                    if before[field] != after[field]:
                        counters[f"changed_{field}"] += 1
            if commit and before != after:
                row.category = after["category"]
                row.tags = after["tags"]
                row.description = after["description"]
                row.visit_duration_minutes = after["visit_duration_minutes"]

        backup_path = None
        if commit and changes:
            backup_path = _backup_path(backup_dir)
            backup_path.write_text(
                json.dumps(
                    {
                        "created_at": datetime.now().isoformat(timespec="seconds"),
                        "province": YUNNAN,
                        "reason": "yunnan-catalog-safe-clean",
                        "rows": [before_rows[change["id"]] for change in changes],
                    },
                    ensure_ascii=False,
                    indent=2,
                    default=str,
                ),
                encoding="utf-8",
            )
            session.commit()

        noise = _noise_candidates(rows)
        public_spaces = _public_space_candidates(rows)
        overlaps = _overlap_candidates(rows)
        origins = Counter(description_origin(post_descriptions[row.id]) for row in rows)
        source_counts = Counter(infer_data_source(row.poi_id) for row in rows)
        return {
            "province": YUNNAN,
            "rows": len(rows),
            "mode": "commit" if commit else "preview",
            "reference_file": str(reference_path),
            "reference_rows": reference_rows,
            "local_knowledge_rows": local_rows,
            "public_knowledge_rows": public_rows,
            "changed_rows": len(changes),
            "changed_fields": {key.removeprefix("changed_"): value for key, value in counters.items() if key.startswith("changed_")},
            "description_origins_after": dict(origins),
            "data_source_inferred": dict(source_counts),
            "noise_candidates": noise,
            "public_space_candidates": public_spaces,
            "overlap_groups": overlaps,
            "backup_path": str(backup_path) if backup_path else None,
            "preview": changes[:40],
            "message": "仅更新已有字段；非景点、普通公园和父子重复未自动删除，请依据清单单独确认。",
        }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--commit", action="store_true", help="写入 MySQL；默认只预览")
    parser.add_argument("--backup-dir", type=Path, default=ROOT / "data" / "backups")
    parser.add_argument(
        "--reference-file",
        type=Path,
        default=ROOT / "data" / "backups" / "yunnan-before-unified-demo-20260917-234658.json",
        help="已有的云南公开资料快照；文件不存在时仍可按本地清单运行",
    )
    args = parser.parse_args()
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    print(json.dumps(clean(commit=args.commit, backup_dir=args.backup_dir, reference_path=args.reference_file), ensure_ascii=False, indent=2, default=str))


if __name__ == "__main__":
    main()
