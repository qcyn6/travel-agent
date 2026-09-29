"""核验并清理海南剩余演示景点。

仅处理能通过公开网页核对的独立景区、文化遗产或保护区；未找到独立实体，或
与同城已保留记录明显重叠的名称直接删除。只更新 attractions 现有字段，不调用
高德、不新增来源或质量字段。默认预览，``--commit`` 才会备份并写入。
"""

from __future__ import annotations

import argparse
from datetime import datetime
import json
from pathlib import Path
import sys
from typing import Any

from sqlalchemy import select

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from backend.config import get_settings  # noqa: E402
from backend.database import init_database, open_session  # noqa: E402
from backend.database.tables import AttractionRow, CityRow, ProvinceRow  # noqa: E402
from backend.rag.vector_store import ChromaAttractionIndex  # noqa: E402


PROVINCE = "海南省"


def item(*, city: str, name: str, new_poi_id: str, category: str, tags: list[str], duration: int, description: str, source_urls: list[str], new_name: str | None = None) -> dict[str, Any]:
    return {"city": city, "name": name, "new_poi_id": new_poi_id, "new_name": new_name or name, "category": category, "tags": tags, "duration": duration, "description": description, "source_urls": source_urls}


VERIFIED: dict[str, dict[str, Any]] = {
    "demo-460300-01": item(
        city="三沙市", name="永兴岛海岛风光", new_poi_id="verified-hn-sansha-yongxing", category="滨水景观", tags=["自然", "滨水", "生态", "摄影"], duration=150,
        description="永兴岛是三沙市政府驻地，岛上有热带海岛环境、环岛道路和海洋相关人文设施，适合在官方开放范围内进行海岛风光与海疆文化体验。登岛和参观须遵守航线、天气及管理要求。",
        source_urls=["https://www.caac.gov.cn/PHONE/ZTZL/RDZT/LHDBRYMH/201603/t20160318_29958.html", "https://paper.people.com.cn/rmrbhwb/page/2016-07/30/12/rmrbhwb2016073012.pdf"],
    ),
    "demo-460300-02": item(
        city="三沙市", name="西沙海洋博物馆", new_poi_id="verified-hn-sansha-marine-museum", category="博物馆与展馆", tags=["历史文化", "博物馆", "研学", "滨水"], duration=90,
        description="西沙海洋博物馆位于永兴岛，以西沙海洋生物标本、图片资料和海洋环境展示为主要内容。开放安排受驻岛管理与航线条件影响，规划时应以官方通知为准。",
        source_urls=["https://www.caac.gov.cn/PHONE/ZTZL/RDZT/LHDBRYMH/201603/t20160318_29958.html", "https://paper.people.com.cn/rmrbhwb/page/2016-07/30/12/rmrbhwb2016073012.pdf"],
    ),
    "demo-460300-03": item(
        city="三沙市", name="鸭公岛生态体验区", new_poi_id="verified-hn-sansha-yagong", category="滨水景观", tags=["自然", "滨水", "生态", "摄影"], duration=120,
        description="鸭公岛是西沙永乐群岛邮轮航线的登岛点，以珊瑚岛礁、浅海和渔民生活体验为主要内容。登岛时间和活动范围须服从邮轮及海岛管理安排。",
        source_urls=["https://www-nanhai.vonechina.com/nhc/destination", "https://paper.people.com.cn/rmrbhwb/page/2016-07/30/12/rmrbhwb2016073012.pdf"],
    ),
    "demo-460300-04": item(
        city="三沙市", name="全富岛海岸景观", new_poi_id="verified-hn-sansha-quanfu", category="滨水景观", tags=["自然", "滨水", "摄影", "休闲"], duration=120,
        description="全富岛位于西沙永乐环礁北缘，是邮轮航线的登岛点，以细沙沙洲、珊瑚礁和透明海水景观为主要体验。活动受潮汐、天气及海岛保护要求约束。",
        source_urls=["https://www-nanhai.vonechina.com/nhc/destination", "https://paper.people.com.cn/rmrbhwb/page/2016-07/30/12/rmrbhwb2016073012.pdf"],
    ),
    "demo-469024-01": item(
        city="临高县", name="临高角解放公园", new_poi_id="verified-hn-lingao-jiefang", category="历史古迹", tags=["历史文化", "红色文化", "滨水", "研学"], duration=120,
        description="临高角解放海南纪念园以海南战役登陆史迹、纪念设施和临高角灯塔为主要内容，适合红色历史学习与海岸纪念游览。",
        source_urls=["https://tyt.hainan.gov.cn/twjr/jnss1/202003/b84f2618e48340c997930336e9dad62a.shtml?ddtab=true", "https://www.hainan.gov.cn/hainan/szfwj/202509/25b913a33e874768b314ca05349400ca.shtml"],
        new_name="临高角解放海南纪念园",
    ),
    "demo-469027-02": item(
        city="乐东黎族自治县", name="莺歌海盐场", new_poi_id="verified-hn-ledong-yinggehai-salt", category="综合景区", tags=["自然", "滨水", "人文", "摄影"], duration=150,
        description="莺歌海盐场是海南西南海岸的盐业生产与盐文化体验地，可结合盐田景观、盐业历史和海岸风光进行慢游；具体可参观区域以现场安排为准。",
        source_urls=["https://www.dx.hainan.gov.cn/130/content_15736.html", "https://zh.explorehainan.com/ft/tansuo/ledong.shtml"],
    ),
    "demo-469027-03": item(
        city="乐东黎族自治县", name="龙沐湾滨海旅游区", new_poi_id="verified-hn-ledong-longmuwan", category="滨水景观", tags=["自然", "滨水", "摄影", "休闲"], duration=150,
        description="龙沐湾是乐东西部海岸的滨海湾区，以沙滩、海岸线和日落景观为主要体验，适合海边漫步、摄影和休闲。水上活动须遵守现场安全提示。",
        source_urls=["https://agri.hainan.gov.cn/hnsnyt/ywdt/zwdt/202504/t20250430_3857817.html", "https://zh.explorehainan.com/ft/tansuo/ledong.shtml"],
    ),
    "demo-469027-04": item(
        city="乐东黎族自治县", name="毛公山景区", new_poi_id="verified-hn-ledong-maogongshan", category="自然山水", tags=["自然", "山地", "地质景观", "摄影"], duration=180,
        description="毛公山以山体天然造型、山地景观和雅亮河谷为主要体验，适合观景、摄影和短线自然漫游；开放状态及登山路线需现场确认。",
        source_urls=["https://zh.explorehainan.com/ft/tansuo/ledong.shtml", "https://agri.hainan.gov.cn/hnsnyt/ywdt/zwdt/202504/t20250430_3857817.html"],
    ),
    "demo-469027-05": item(
        city="乐东黎族自治县", name="黄流老街", new_poi_id="verified-hn-ledong-huangliu", category="古镇与街区", tags=["历史文化", "人文", "城市漫游", "美食"], duration=120,
        description="黄流镇具有较长历史和地方民间艺术传统，老街体验以街巷漫游、地方生活和特色饮食为主，适合慢节奏城市文化观察。",
        source_urls=["https://zh.explorehainan.com/ft/tansuo/ledong.shtml", "https://www.hainan.gov.cn/hainan/mstc/202004/fec1d72e38334cb0bb680622c9d5470e.shtml?ddtab=true"],
    ),
    "demo-469022-01": item(
        city="屯昌县", name="木色旅游度假区", new_poi_id="verified-hn-tunchang-muse", category="休闲度假", tags=["自然", "滨水", "休闲", "摄影"], duration=150,
        description="木色湖及周边是屯昌中部的湖泊与乡村休闲资源，适合湖畔观景、短途漫游和周边生态体验。具体游览区域以当地开放安排为准。",
        source_urls=["https://www.hainanjk.com/wx/View.aspx?id=4096"],
        new_name="木色湖旅游度假区",
    ),
    "demo-469022-02": item(
        city="屯昌县", name="梦幻香山芳香文化园", new_poi_id="verified-hn-tunchang-menghuanxiangshan", category="休闲度假", tags=["休闲", "生态", "乡村", "研学"], duration=180,
        description="梦幻香山芳香文化园集现代农业种植、乡村休闲和香文化体验于一体，适合园区漫游、亲子体验和农业研学。",
        source_urls=["https://www.menghuanxiangshan.com/", "https://tunchang.hainan.gov.cn/tcly/lyzx/202401/t20240115_3569579.html"],
    ),
    "demo-469022-03": item(
        city="屯昌县", name="加乐潭生态旅游区", new_poi_id="verified-hn-tunchang-jialetan", category="综合景区", tags=["自然", "滨水", "生态", "休闲"], duration=150,
        description="加乐潭是屯昌乡村生态旅游线路中的湖潭型休闲点，适合水岸观景、乡村漫游和短时休息，具体开放设施以当地安排为准。",
        source_urls=["https://www.hainanjk.com/wx/View.aspx?id=4096"],
    ),
    "demo-469022-04": item(
        city="屯昌县", name="屯昌油画步行街", new_poi_id="verified-hn-tunchang-oil-painting", category="古镇与街区", tags=["人文", "城市漫游", "摄影", "夜游"], duration=90,
        description="屯昌油画步行街是县城文化街区，适合街区漫游、艺术主题体验和夜间休闲；活动与商铺开放时间以现场为准。",
        source_urls=["https://tunchang.hainan.gov.cn/tcly/", "https://tunchang.hainan.gov.cn/tcly/lyzx/"],
    ),
    "demo-469026-01": item(
        city="昌江黎族自治县", name="棋子湾旅游度假区", new_poi_id="verified-hn-changjiang-qiziwan", category="休闲度假", tags=["自然", "滨水", "摄影", "休闲"], duration=180,
        description="棋子湾以礁石海岸、沙滩、海湾和日落景观为主要体验，适合海岸漫步、摄影、露营或休闲度假，水上活动须遵守现场安全提示。",
        source_urls=["https://www.mee.gov.cn/home/ztbd/2021/mlhwyxalzjhd/algs/hns2/202109/t20210906_900103.shtml", "https://www.hainan.gov.cn/hainan/mstc/202004/fec1d72e38334cb0bb680622c9d5470e.shtml?ddtab=true"],
    ),
    "demo-469026-03": item(
        city="昌江黎族自治县", name="昌化古城", new_poi_id="verified-hn-changjiang-changhua", category="历史古迹", tags=["历史文化", "古建", "人文", "城市漫游"], duration=120,
        description="古昌化城是昌江西部历史文化资源的一部分，适合结合古城遗迹、地方街巷和昌化江流域历史进行人文漫游。具体遗址开放范围以现场管理为准。",
        source_urls=["https://www.hainan.gov.cn/hainan/mstc/202004/fec1d72e38334cb0bb680622c9d5470e.shtml?ddtab=true", "https://www.hainan.gov.cn/hainan/szfbgtwj/201711/d51db282e9f34950a4f360be54e2fcba.shtml?ddtab=true"],
        new_name="古昌化城",
    ),
    "demo-469026-04": item(
        city="昌江黎族自治县", name="石碌铁矿工业遗产园", new_poi_id="verified-hn-changjiang-shilu", category="综合景区", tags=["历史文化", "人文", "研学", "建筑"], duration=150,
        description="石碌铁矿及其工业遗产公园以矿业生产遗迹、地质景观和工业历史为主要体验，适合工业遗产参观与研学。开放区域和安全要求以现场公告为准。",
        source_urls=["https://www.hainan.gov.cn/hainan/szfbgtwj/201711/d51db282e9f34950a4f360be54e2fcba.shtml?ddtab=true", "https://www.hainan.gov.cn/hainan/sxgw/202211/d417713786da4d90bff8eee8d957ac02/files/cc9d2c2849a544059a6dcf522b72aeb7.pdf"],
        new_name="石碌铁矿工业文化遗产公园",
    ),
    "demo-469026-05": item(
        city="昌江黎族自治县", name="海尾湿地公园", new_poi_id="verified-hn-changjiang-haiwei", category="生态动植物", tags=["自然", "生态", "滨水", "摄影"], duration=150,
        description="海尾湿地公园位于棋子湾延伸海岸线上，以湿地、芦苇、水鸟和滨海生态系统为主要体验，适合观鸟、摄影和生态科普。",
        source_urls=["https://www.mee.gov.cn/home/ztbd/2021/mlhwyxalzjhd/algs/hns2/202109/t20210906_900103.shtml", "https://www.hainan.gov.cn/hainan/mstc/202004/fec1d72e38334cb0bb680622c9d5470e.shtml?ddtab=true"],
    ),
    "demo-469023-01": item(
        city="澄迈县", name="福山咖啡文化风情镇", new_poi_id="verified-hn-chengmai-fushan-coffee", category="休闲度假", tags=["休闲", "乡村", "人文", "美食"], duration=150,
        description="福山咖啡文化风情镇以咖啡种植、加工、品鉴和农旅体验为主要内容，适合咖啡文化了解、乡村休闲和特色美食体验。",
        source_urls=["https://www.hainan.gov.cn/hainan/xgwjtv/202502/6f09aeb6e899441caa9b66167b498b66.shtml", "https://agri.hainan.gov.cn/hnsnyt/ywdt/zwdt/202504/t20250414_3848428.html"],
    ),
    "demo-469023-03": item(
        city="澄迈县", name="美榔双塔", new_poi_id="verified-hn-chengmai-meilang", category="历史古迹", tags=["历史文化", "古建", "人文", "建筑"], duration=90,
        description="美榔双塔是澄迈保存的宋代古塔和全国重点文物保护单位，适合古建筑、文物与地方历史文化参观。",
        source_urls=["https://zhuanti.mct.gov.cn/xcss2024_xcygj/hainan/detail/7175.html", "https://www.hainan.gov.cn/hainan/szfbgtwj/201711/d51db282e9f34950a4f360be54e2fcba.shtml?ddtab=true"],
    ),
    "demo-469023-04": item(
        city="澄迈县", name="红树林湿地公园", new_poi_id="verified-hn-chengmai-mangrove", category="生态动植物", tags=["自然", "生态", "滨水", "研学"], duration=150,
        description="海南红树湾湿地公园以红树林湿地、木栈道、自然教育和候鸟观察为主要体验，适合生态科普、亲子休闲和滨水漫游。",
        source_urls=["https://zhuanti.mct.gov.cn/xcss2024_xcygj/hainan/detail/7175.html"],
        new_name="海南红树湾湿地公园",
    ),
    "demo-469023-05": item(
        city="澄迈县", name="盈滨海岸", new_poi_id="verified-hn-chengmai-yingbin", category="滨水景观", tags=["自然", "滨水", "休闲", "摄影"], duration=120,
        description="盈滨内海及其海岸带是澄迈老城一带的潮汐港湾与海洋湿地，适合滨水观景和生态观察；相关区域应遵守生态保护与岸线管理要求。",
        source_urls=["https://www.mee.gov.cn/xxgk2018/xxgk/xxgk15/201908/t20190809_713908.html"],
    ),
    "demo-469030-01": item(
        city="琼中黎族苗族自治县", name="黎母山国家森林公园", new_poi_id="verified-hn-qiongzhong-limushan", category="生态动植物", tags=["自然", "森林", "山地", "生态"], duration=240,
        description="黎母山是琼中热带雨林国家公园的重要片区，以山地雨林、溪流和生态徒步为主要体验，适合自然观察、森林漫游和研学。",
        source_urls=["https://www.forestry.gov.cn/lyj/1/dfdt/20250916/642410.html", "https://www.forestry.gov.cn/u/cms/www/202404/12181420x55l.pdf"],
    ),
    "demo-469030-02": item(
        city="琼中黎族苗族自治县", name="百花岭热带雨林文化旅游区", new_poi_id="verified-hn-qiongzhong-baihualing", category="自然山水", tags=["自然", "森林", "山地", "生态", "摄影"], duration=240,
        description="百花岭热带雨林文化旅游区以山地雨林、瀑布和黎苗文化体验为主要内容，适合雨林观光、徒步与生态休闲。",
        source_urls=["https://lyj.hainan.gov.cn/ztzl/cyyl/202502/t20250207_3812491.html", "https://zhuanti.mct.gov.cn/xczgshsh2023/cxyh/detail_cha/3863.html"],
    ),
    "demo-469030-03": item(
        city="琼中黎族苗族自治县", name="什寒村", new_poi_id="verified-hn-qiongzhong-shihan", category="民俗文化", tags=["民族文化", "乡村", "人文", "生态"], duration=150,
        description="什寒村保留黎苗特色民居与乡村生活场景，适合村落漫游、民族文化体验和生态休闲；参与民俗活动应尊重当地社区安排。",
        source_urls=["https://lyj.hainan.gov.cn/ztzl/cyyl/202502/t20250207_3812491.html", "https://www.ctnews.com.cn/paper/att/202109/24/1833c642-b385-461b-b66b-10b7ef7d7d9f.pdf"],
    ),
    "demo-469030-04": item(
        city="琼中黎族苗族自治县", name="鹦哥岭自然保护区", new_poi_id="verified-hn-qiongzhong-yinggeling", category="生态动植物", tags=["自然", "森林", "生态", "研学"], duration=180,
        description="鹦哥岭是海南热带雨林国家公园的重要生态片区，以热带雨林、生物多样性和自然科考体验为主，进入保护区须遵守保护管理要求。",
        source_urls=["https://www.forestry.gov.cn/u/cms/www/202404/12181420x55l.pdf", "https://zhuanti.mct.gov.cn/xczgshsh2023/cxyh/detail_cha/3863.html"],
    ),
    "demo-469025-01": item(
        city="白沙黎族自治县", name="邦溪自然保护区", new_poi_id="verified-hn-baisha-bangxi", category="生态动植物", tags=["自然", "生态", "森林", "摄影"], duration=180,
        description="邦溪片区属于白沙重要生态保护资源，适合在允许的开放区域进行森林、湿地与野生动物生态观察；保护区核心区域不作为普通游览线路。",
        source_urls=["https://www.spp.gov.cn/dfjcdt/201607/t20160714_142826.shtml", "https://baisha.hainan.gov.cn/baisha/index.html"],
    ),
    "demo-469025-04": item(
        city="白沙黎族自治县", name="白沙茶园观光区", new_poi_id="verified-hn-baisha-tea", category="休闲度假", tags=["自然", "生态", "乡村", "研学"], duration=150,
        description="白沙陨石坑绿茶采摘基地将茶产业、茶园观光和黎族文化体验结合，适合采茶、茶文化了解与乡村休闲。具体采摘季和开放安排以基地通知为准。",
        source_urls=["https://zhuanti.mct.gov.cn/xczgshsh2023/cxyh/detail_cha/3863.html", "https://baisha.hainan.gov.cn/baisha/index.html"],
        new_name="白沙陨石坑绿茶采摘基地",
    ),
}


DELETE: dict[str, dict[str, str]] = {
    "demo-460300-05": {"city": "三沙市", "name": "西沙群岛海洋观景带", "reason": "区域性概念，未找到可对应单一景区入口的独立 POI。"},
    "demo-469024-03": {"city": "临高县", "name": "百仞滩瀑布", "reason": "未找到临高县同名独立景区的可靠公开资料；避免用其他县的瀑布替代。"},
    "demo-469024-04": {"city": "临高县", "name": "临高灯塔海岸", "reason": "与已核验的临高角解放海南纪念园/临高角灯塔属于同一地点，删除父子重叠记录。"},
    "demo-469024-05": {"city": "临高县", "name": "金沙湾滨海旅游区", "reason": "未找到与当前县市和名称同时匹配的独立景区资料。"},
    "demo-469022-05": {"city": "屯昌县", "name": "南坤民族风情村", "reason": "未找到可核验的独立景区入口或官方景点实体，避免把行政村概念当景点。"},
    "demo-469030-05": {"city": "琼中黎族苗族自治县", "name": "琼中湾岭茶园", "reason": "未找到与该名称匹配的独立茶园景区资料，已有可核验的乡村茶园记录不强行替代。"},
    "demo-469025-02": {"city": "白沙黎族自治县", "name": "白沙起义纪念园", "reason": "公开资料将同名纪念园定位在琼中红毛镇，无法证明当前白沙县坐标对应该实体，删除疑似错城记录。"},
    "demo-469025-05": {"city": "白沙黎族自治县", "name": "南开河生态景观带", "reason": "区域性景观带而非可核验的独立景区 POI，按规则删除。"},
}


def _backup_path(directory: Path) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    path = directory / f"hainan-remaining-before-{stamp}.json"
    suffix = 1
    while path.exists():
        path = directory / f"hainan-remaining-before-{stamp}-{suffix}.json"
        suffix += 1
    return path


def _payload(row: AttractionRow) -> dict[str, Any]:
    return {column.name: getattr(row, column.name) for column in row.__table__.columns}


def clean(*, commit: bool, backup_dir: Path) -> dict[str, Any]:
    init_database()
    target_ids = tuple(VERIFIED) + tuple(DELETE)
    with open_session() as session:
        pairs = list(session.execute(select(AttractionRow, CityRow).join(CityRow, CityRow.id == AttractionRow.city_id).join(ProvinceRow, ProvinceRow.id == CityRow.province_id).where(ProvinceRow.name == PROVINCE, AttractionRow.poi_id.in_(target_ids)).order_by(AttractionRow.id)).all())
        found = {row.poi_id: (row, city.name) for row, city in pairs}
        missing = sorted(set(target_ids) - set(found))
        mismatches: list[dict[str, Any]] = []
        for poi_id, spec in {**VERIFIED, **DELETE}.items():
            if poi_id in found:
                row, city = found[poi_id]
                if row.name != spec["name"] or city != spec["city"]:
                    mismatches.append({"poi_id": poi_id, "expected": f"{spec['city']} / {spec['name']}", "actual": f"{city} / {row.name}"})
        new_ids = tuple(spec["new_poi_id"] for spec in VERIFIED.values())
        conflicting = [row.poi_id for row in session.scalars(select(AttractionRow).where(AttractionRow.poi_id.in_(new_ids)))]
        if missing or mismatches or conflicting:
            raise RuntimeError(json.dumps({"missing": missing, "mismatches": mismatches, "new_id_conflicts": conflicting}, ensure_ascii=False))
        affected = [found[poi_id][0] for poi_id in target_ids]
        preview = [{"id": row.id, "poi_id": row.poi_id, "city": found[row.poi_id][1], "name": row.name, "action": "verify" if row.poi_id in VERIFIED else "delete"} for row in affected]
        if not commit:
            return {"province": PROVINCE, "mode": "preview", "verified_replacements": len(VERIFIED), "deletions": len(DELETE), "affected": preview, "source_urls": sorted({url for spec in VERIFIED.values() for url in spec["source_urls"]}), "message": "仅预览；传入 --commit 才会备份并写入。未调用高德。"}
        backup_path = _backup_path(backup_dir)
        backup_path.write_text(json.dumps({"created_at": datetime.now().isoformat(timespec="seconds"), "province": PROVINCE, "reason": "public-source-verified-hainan-remaining", "rows": [_payload(row) for row in affected], "verified_sources": {poi_id: spec["source_urls"] for poi_id, spec in VERIFIED.items()}, "deletion_reasons": DELETE}, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
        for old_poi_id, spec in VERIFIED.items():
            row = found[old_poi_id][0]
            row.poi_id = spec["new_poi_id"]
            row.name = spec["new_name"]
            row.category = spec["category"]
            row.tags = spec["tags"]
            row.description = spec["description"]
            row.visit_duration_minutes = spec["duration"]
        for poi_id in DELETE:
            session.delete(found[poi_id][0])
        session.commit()

    vectors_deleted = 0
    vector_error = None
    try:
        settings = get_settings()
        index = ChromaAttractionIndex(settings.chroma_path, collection_name=settings.chroma_collection)
        old_ids = list(VERIFIED) + list(DELETE)
        index.collection.delete(ids=old_ids)
        vectors_deleted = len(old_ids)
    except Exception as exc:  # pragma: no cover
        vector_error = f"{type(exc).__name__}: {exc}"
    return {"province": PROVINCE, "mode": "commit", "verified_replacements": len(VERIFIED), "deletions": len(DELETE), "affected": preview, "backup_path": str(backup_path), "vectors_deleted": vectors_deleted, "vector_error": vector_error, "message": "海南剩余演示记录已按公开资料核验/删除；未调用高德，未新增数据库字段。"}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--commit", action="store_true", help="备份并写入数据库；默认只预览")
    parser.add_argument("--backup-dir", type=Path, default=ROOT / "data" / "backups")
    args = parser.parse_args()
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    print(json.dumps(clean(commit=args.commit, backup_dir=args.backup_dir), ensure_ascii=False, indent=2, default=str))


if __name__ == "__main__":
    main()
