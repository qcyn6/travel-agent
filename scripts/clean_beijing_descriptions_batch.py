"""用公开文旅资料替换北京景点的模板简介。

本批只更新 ``attractions.description``，不新增字段、不调用高德；保留现有
名称、坐标、标签和游览时长。默认预览，``--commit`` 才写入并备份，写入后
需运行 ``rebuild_province_vectors.py --province 北京市 --commit``。

核验入口主要使用北京市文化和旅游局景点目录、首都之窗和北京旅游网：
https://whlyj.beijing.gov.cn/ggfw/ly/202511/t20251119_4287815.html
https://s.visitbeijing.com.cn/attraction/101936
https://s.visitbeijing.com.cn/attraction/101944
https://s.visitbeijing.com.cn/attraction/119454
https://s.visitbeijing.com.cn/attraction/120994
https://s.visitbeijing.com.cn/attraction/120979
https://s.visitbeijing.com.cn/attraction/120603
https://s.visitbeijing.com.cn/attraction/118181
https://www.beijing.gov.cn/renwen/rwzyd/qxdw/myhdlzqzh/ymyyzhgy/202309/t20230921_3263781.html
https://www.beijing.gov.cn/renwen/bjgk/hrgk/wl/202212/t20221220_2881074.html
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


SOURCE_URLS = [
    "https://whlyj.beijing.gov.cn/ggfw/ly/202511/t20251119_4287815.html",
    "https://s.visitbeijing.com.cn/attraction/101936",
    "https://s.visitbeijing.com.cn/attraction/101944",
    "https://s.visitbeijing.com.cn/attraction/119454",
    "https://s.visitbeijing.com.cn/attraction/120994",
    "https://s.visitbeijing.com.cn/attraction/120979",
    "https://s.visitbeijing.com.cn/attraction/120603",
    "https://s.visitbeijing.com.cn/attraction/118181",
    "https://www.beijing.gov.cn/renwen/rwzyd/qxdw/myhdlzqzh/ymyyzhgy/202309/t20230921_3263781.html",
    "https://www.beijing.gov.cn/renwen/bjgk/hrgk/wl/202212/t20221220_2881074.html",
]


def _text(name: str, body: str) -> str:
    # 每条 body 已以景点名称开头；这里不再重复拼接名称。
    return f"{body}开放时间、预约和临时管控以景区官方公告为准。"


VERIFIED: dict[str, str] = {
    "mct-4a-299": _text("世界公园景区", "以世界各地代表性建筑和园林景观的缩微展示为主要内容，适合建筑观察、主题摄影和城市休闲。"),
    "mct-4a-2713": _text("世界花卉大观园景区", "位于北京南四环，园区由温室和多个花园组成，集中展示中外花卉、珍稀植物和园林景观，兼具科普与休闲功能。"),
    "mct-4a-3080": _text("东城区龙潭公园景区", "以龙潭湖水面、龙文化主题园林、桥亭和绿地为主要景观，是北京城东南的城市公园和传统节庆活动空间。"),
    "mct-4a-3451": _text("中国人民抗日战争纪念馆景区", "以中国人民抗日战争历史展陈、文物和史料为主要内容，适合历史文化参观、纪念教育和研学。"),
    "mct-4a-2683": _text("中国航空博物馆旅游区", "以航空器收藏、航空科技展示和航空发展史教育为主要内容，适合航空主题参观与科普研学。"),
    "mct-4a-1086": _text("中央电视塔景区", "中央电视塔是北京西部的城市地标，景区以塔体建筑、城市观景和广播电视文化展示为主要体验。"),
    "mct-4a-1291": _text("丰台区中国园林博物馆景区", "以中国园林历史、造园艺术、园林文物和室内外展园为主要内容，适合园林文化参观与研学。"),
    "mct-4a-1599": _text("丰台区北宫森林公园景区", "以山地森林、林间步道和近郊自然景观为主要内容，适合散步、摄影和轻户外活动。"),
    "mct-4a-19": _text("乐多港假日广场景区", "由奥特莱斯商业空间和奇幻乐园等业态组成，将历史文化主题、影视科技体验与休闲消费结合，适合安排半日休闲。"),
    "B000A7O5PK": _text("什刹海", "什刹海由前海、后海和西海及周边胡同、历史建筑组成，适合湖畔漫步、胡同文化观察、摄影和夜间休闲。"),
    "mct-4a-863": _text("什刹海风景区", "以什刹海水面、传统胡同和老北京历史风貌为主要内容，适合步行游览、城市漫游和人文摄影。"),
    "mct-4a-2815": _text("仙居谷风景区", "位于密云水库上游安达木河畔，景区有森林、溪流、泉潭和山地步道，适合自然观赏、踏青和轻徒步。"),
    "mct-4a-1509": _text("八奇洞景区", "位于门头沟潭柘寺附近，洞体全长约1350米，以褶皱形洞道和八大天然景观为主要看点，适合地质景观观赏。"),
    "mct-5a-34783": _text("八达岭—慕田峪长城旅游区", "由北京北部两段著名长城景观组成，核心体验是明代长城关隘、敌楼、山地景观和长城历史文化。"),
    "mct-4a-446": _text("八达岭水关长城景区", "水关长城位于八达岭段东端，长城、古关口、山地和京张铁路历史共同构成景区主要体验。"),
    "B000A45467": _text("八达岭长城", "八达岭长城是明长城的重要关隘段，保存有连续城墙、敌楼和山脊景观，适合长城历史参观和山地摄影。"),
    "B0I2DHO7RX": _text("北京（通州）大运河文化旅游景区", "景区位于北京城市副中心北运河沿线，全长约12.1公里，串联运河水系、燃灯塔、码头、公园和历史文化景观。"),
    "mct-4a-2702": _text("北京中国紫檀博物馆景区", "以紫檀艺术收藏、传统古典家具陈列和中国木作文化研究展示为主要内容，适合室内人文参观。"),
    "mct-4a-479": _text("北京八达岭世界葡萄博览中心景区", "以葡萄品种展示、葡萄文化科普和园区农业休闲体验为主要内容，适合近郊自然观赏和亲子研学。"),
    "B000A87IUX": _text("北京动物园", "以动物饲养展示、科普教育和园林游览为主要内容，是北京市区内的综合性动物园，适合亲子和研学。"),
    "B0I13APEW5": _text("北京奥林匹克公园", "集中分布鸟巢、水立方等奥运场馆及公共开放空间，适合建筑摄影、城市漫游和大型体育文化主题参观。"),
    "B000A7N4BI": _text("北京欢乐谷", "以大型主题游乐设施、演艺和节庆活动为主要内容，适合主题娱乐和亲子休闲，游览强度取决于项目选择。"),
    "B000A831SV": _text("北京野生动物园", "以野生动物展示、动物科普和园区游览为主要内容，适合亲子自然观察和研学活动。"),
    "B000A80UL1": _text("北海公园", "北海公园是北京传统皇家园林，以北海水面、琼华岛、白塔和古典园林建筑为主要景观，适合园林漫游。"),
    "B000A856WS": _text("南海子公园", "南海子公园以湿地水面、草地、林地和城市生态休闲空间为主要内容，适合散步、摄影和亲子游览。"),
    "B0FFFAH7I9": _text("南锣鼓巷", "南锣鼓巷及周边胡同保存老北京街巷格局，集合传统民居、文创商铺和餐饮，适合步行漫游与城市文化观察。"),
    "B000AABXEH": _text("古北水镇", "古北水镇位于密云古北口镇，背靠司马台长城、依托鸳鸯湖水库，融合山水、北方民居街区、夜游和度假休闲。"),
    "mct-5a-34787": _text("圆明园遗址公园景区", "圆明园由圆明、长春、绮春三园组成，遗址、湖泊园林和历史展陈共同呈现清代皇家园林遗址文化。"),
    "B000A81CB2": _text("天坛公园", "天坛是明清皇家祭天建筑群，祈年殿、圜丘等礼制建筑与古柏园林构成主要游览内容，适合建筑和历史文化参观。"),
    "B000A60DA1": _text("天安门", "天安门位于北京中轴线南段，是明清皇城重要门楼和北京城市地标，适合结合广场及周边博物馆进行人文参观。"),
    "B000A83C1S": _text("天安门广场", "天安门广场是北京中轴线上的大型公共空间，包含国旗、人民英雄纪念碑等重要城市纪念设施，开放和参观需遵守现场管理。"),
    "B000A87QJV": _text("奥林匹克公园中心区", "中心区集中分布奥运场馆、公共广场和城市景观节点，适合建筑摄影、夜景观赏和城市漫游。"),
    "B000A875MC": _text("奥林匹克森林公园", "奥林匹克森林公园以大面积林地、湖泊、草地和步道为主要内容，是北京北部适合散步、跑步和摄影的城市生态公园。"),
    "B000A645D9": _text("恭王府博物馆", "恭王府以清代王府建筑、宅院布局和园林遗存为主要内容，适合古建观察、历史文化参观和博物馆游览。"),
    "B000A09F5A": _text("慕田峪长城", "慕田峪长城东连古北口、西接居庸关，保存正关台、大角楼等山脊敌楼和城墙景观，适合长城历史参观与山地摄影。"),
    "B000A8UIN8": _text("故宫博物院", "故宫博物院位于北京中轴线中心，是明清宫殿建筑群和国家级博物馆，收藏大量宫廷文物，参观需提前预约。"),
    "B000A84GDN": _text("故宫博物院-午门", "午门是故宫南端的主要门楼和参观入口之一，建筑形制、城门空间及相关展陈构成故宫参观的一部分。"),
    "B000A886RE": _text("明十三陵景区", "明十三陵是明代皇帝陵寝集中分布区，长陵、定陵等陵寝建筑、神道和山地环境构成主要历史文化景观。"),
    "B000A7I1OL": _text("景山公园", "景山公园位于故宫北侧，以景山山体、古典园林和俯瞰北京中轴线的观景视野为主要体验。"),
    "B000A7CA07": _text("朝阳公园", "朝阳公园是北京东部大型城市公园，以湖泊、草地、林地和公共休闲活动空间为主要内容，适合散步和摄影。"),
    "B000A0B593": _text("紫竹院公园", "紫竹院公园以竹景、水面、桥亭和园林步道为主要内容，适合城市休闲、赏景和短时漫游。"),
    "B000A7BGMG": _text("雍和宫", "雍和宫是北京重要的藏传佛教寺院，殿宇、佛教造像和宗教文化遗存构成主要参观内容，参观应遵守寺院礼仪。"),
    "B000A7O1CU": _text("颐和园", "颐和园是以昆明湖、万寿山和长廊等景观组成的皇家园林，山水格局、古典建筑和园林遗产构成主要体验。"),
    "B000A7O684": _text("香山公园", "香山公园以山地森林、古迹园林和秋季红叶景观著称，适合登山、自然观赏和季节性摄影。"),
    "B0FFFS33G2": _text("鼓楼", "北京鼓楼是中轴线北段的传统城市楼阁，与钟楼共同构成老北京报时建筑体系，适合建筑和城市历史参观。"),
}


def _payload(row: AttractionRow) -> dict[str, Any]:
    return {column.name: getattr(row, column.name) for column in row.__table__.columns}


def clean(*, commit: bool, backup_dir: Path) -> dict[str, Any]:
    init_database()
    with open_session() as session:
        rows = list(session.scalars(select(AttractionRow).where(AttractionRow.poi_id.in_(tuple(VERIFIED))).order_by(AttractionRow.id)))
        found = {row.poi_id: row for row in rows}
        missing = sorted(set(VERIFIED) - set(found))
        if missing:
            raise RuntimeError(json.dumps({"missing": missing}, ensure_ascii=False))
        changed = [{"poi_id": row.poi_id, "name": row.name, "before": row.description, "after": VERIFIED[row.poi_id]} for row in rows if row.description != VERIFIED[row.poi_id]]
        if not commit:
            return {"province": "北京市", "mode": "preview", "records": len(rows), "changed": len(changed), "sources": SOURCE_URLS, "sample": changed[:5], "message": "仅预览；传入 --commit 才写入。"}
        backup_dir.mkdir(parents=True, exist_ok=True)
        backup_path = backup_dir / f"beijing-descriptions-before-{datetime.now().strftime('%Y%m%d-%H%M%S')}.json"
        backup_path.write_text(json.dumps({"created_at": datetime.now().isoformat(timespec="seconds"), "province": "北京市", "source_urls": SOURCE_URLS, "rows": [_payload(row) for row in rows]}, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
        for row in rows:
            row.description = VERIFIED[row.poi_id]
        session.commit()

    vectors_deleted = 0
    vector_error = None
    try:
        settings = get_settings()
        index = ChromaAttractionIndex(settings.chroma_path, collection_name=settings.chroma_collection)
        index.collection.delete(ids=list(VERIFIED))
        vectors_deleted = len(VERIFIED)
    except Exception as exc:  # pragma: no cover
        vector_error = f"{type(exc).__name__}: {exc}"
    return {"province": "北京市", "mode": "commit", "records": len(rows), "changed": len(changed), "backup_path": str(backup_path), "vectors_deleted": vectors_deleted, "vector_error": vector_error, "sources": SOURCE_URLS, "message": "已用公开文旅资料替换北京景点简介；未调用高德、未新增字段。"}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--commit", action="store_true")
    parser.add_argument("--backup-dir", type=Path, default=ROOT / "data" / "backups")
    args = parser.parse_args()
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    print(json.dumps(clean(commit=args.commit, backup_dir=args.backup_dir), ensure_ascii=False, indent=2, default=str))


if __name__ == "__main__":
    main()
