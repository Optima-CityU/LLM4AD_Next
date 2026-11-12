//! 投影层折叠(v0.6 Step 2) — 纯确定性投影,零模型参与,零App特判。
//! 同一棵树永远折出同一份渲染;class 只做相等比较,本文件不含任何具体控件类名/包名。
//! 规则(先在真实dump上打样验证,tests 用四张真实页面钉死形状):
//!   R1 列表项判定,两个触发器取并:
//!      T1(语义) scrollable 容器的直接子节点即列表项——系统自己声明的列表面,
//!        异构卡片(不同wrapper class的信息流)也覆盖。两道护栏: 单子容器不算
//!        (ScrollView套整页单列),与容器同框的满幅子项不折(ViewPager页/包装层);
//!      T2(结构) 同父同class兄弟 ≥REPEAT_K 个——覆盖未标scrollable的重复区块
//!   R2 列表项中,子树文字 ≥RICH_N 条或 ≥RICH_C 字的成员各自折叠成头行(逐成员判——
//!      分隔条/简单项不折;设置页菜单行因此天然全保留,agent 主工作面无损)
//!   R3 scrollable 且 ≥2 直接子项 → 前置容器注解行(共N项↕)
//!   R4 clickable+有id+子树无文字 → 图标行(此前对模型完全不可见)
//!   R5 其余文字行照旧,40字截断与 els 契约对齐
//! 安全性质: 折叠永不藏交互把手——头行=子树中面积最大的文字(人与模型本来就点的锚);
//! 每道折缝标注(折N条M字);被折内容仍在全量层,what=点名照常吸附。折叠≠过滤,缝看得见。
use crate::device::FullNode;

pub const REPEAT_K: usize = 3; // 同类兄弟达此数即为列表(结构自相似的最小样本量)
pub const RICH_N: usize = 4;   // 子树文字条数达此值=复合卡片(菜单行只有1~3条,不会误伤)
pub const RICH_C: usize = 60;  // 或子树文字总字数达此值(长文卡只有1~2个节点却上千字)
const HEAD_CAP: usize = 24;    // 头行文字截断
const LINE_CAP: usize = 40;    // 普通行截断(与 els 的40字规则对齐)

#[derive(PartialEq, Eq, Clone, Copy, Debug)]
pub enum Kind { Plain, Head, List, Icon }

pub struct Line {
    pub t: String,   // 展示文本(不含坐标;坐标由调用方按空间换算追加)
    pub b: [i32; 4], // 设备像素框(头行=头文字的框,注解行=容器的框)
    pub kind: Kind,
}

fn cut(s: &str, n: usize) -> String {
    s.chars().take(n).collect()
}

fn short_class(c: &str) -> &str {
    c.rsplit('.').next().unwrap_or(c)
}

/// 子树内文字节点索引(含自身,文档序)
fn texts_in(i: usize, full: &[FullNode], kids: &[Vec<usize>], acc: &mut Vec<usize>) {
    if !full[i].t.is_empty() { acc.push(i); }
    for &k in &kids[i] { texts_in(k, full, kids, acc); }
}

fn area(b: [i32; 4]) -> i64 {
    (b[2] - b[0]).max(0) as i64 * (b[3] - b[1]).max(0) as i64
}

fn emit(i: usize, full: &[FullNode], kids: &[Vec<usize>], out: &mut Vec<Line>) {
    // R2 富判定: 子树文字条数或总字数任一达标(长文卡只有1~2个节点却上千字)
    let rich = |k: usize| {
        let mut acc = Vec::new();
        texts_in(k, full, kids, &mut acc);
        let chars: usize = acc.iter().map(|&t| full[t].t.chars().count()).sum();
        acc.len() >= RICH_N || chars >= RICH_C
    };
    // R1: 找出本节点的可折叠子项(kids[i] 内的下标)
    let mut folded = vec![false; kids[i].len()];
    // T2(结构): 同class兄弟 ≥REPEAT_K
    let mut by: std::collections::HashMap<&str, Vec<usize>> = Default::default();
    for (j, &k) in kids[i].iter().enumerate() {
        by.entry(full[k].class.as_str()).or_default().push(j);
    }
    for g in by.values() {
        if g.len() >= REPEAT_K {
            for &j in g {
                if rich(kids[i][j]) { folded[j] = true; }
            }
        }
    }
    // T1(语义): scrollable 的直接子节点即列表项(异构信息流卡片wrapper class各不相同,
    // 结构自相似兜不住;系统声明的列表面不需要推断)。护栏: 单子容器不算;满幅子项不折
    if full[i].scrollable && kids[i].len() >= 2 {
        for (j, &k) in kids[i].iter().enumerate() {
            if full[k].b != full[i].b && rich(k) { folded[j] = true; }
        }
    }
    // R3: 容器注解
    if full[i].scrollable && kids[i].len() >= 2 {
        let tag = full[i].id.as_deref().unwrap_or_else(|| short_class(&full[i].class));
        out.push(Line {
            t: format!("[列表 {tag} 共{}项↕]", kids[i].len()),
            b: full[i].b, kind: Kind::List,
        });
    }
    // R5/R4: 自身行
    if !full[i].t.is_empty() {
        out.push(Line { t: cut(&full[i].t, LINE_CAP), b: full[i].b, kind: Kind::Plain });
    } else if full[i].clickable {
        if let Some(id) = &full[i].id {
            let mut acc = Vec::new();
            texts_in(i, full, kids, &mut acc);
            if acc.is_empty() {
                out.push(Line { t: format!("[icon {id}]"), b: full[i].b, kind: Kind::Icon });
            }
        }
    }
    // 子节点: 折叠项收成头行,其余递归
    for (j, &k) in kids[i].iter().enumerate() {
        if folded[j] {
            let mut acc = Vec::new();
            texts_in(k, full, kids, &mut acc);
            let head = *acc.iter()
                .max_by_key(|&&t| (area(full[t].b), std::cmp::Reverse(t)))
                .expect("rich 判定保证子树有文字");
            let chars: usize = acc.iter().map(|&t| full[t].t.chars().count()).sum();
            out.push(Line {
                t: format!("▸ {} (折{}条{}字)", cut(&full[head].t, HEAD_CAP), acc.len() - 1, chars),
                b: full[head].b,
                kind: Kind::Head,
            });
        } else {
            emit(k, full, kids, out);
        }
    }
}

/// 树重建(折叠与inspect探针共用): parent = 最近的更浅前驱(文档序 + 深度栈)。
/// 返回 (每节点的子索引表, 根索引表)。
pub fn tree_of(full: &[FullNode]) -> (Vec<Vec<usize>>, Vec<usize>) {
    let mut kids: Vec<Vec<usize>> = vec![Vec::new(); full.len()];
    let mut roots: Vec<usize> = Vec::new();
    let mut stack: Vec<usize> = Vec::new();
    for i in 0..full.len() {
        while stack.last().map_or(false, |&t| full[t].depth >= full[i].depth) { stack.pop(); }
        match stack.last() {
            Some(&p) => kids[p].push(i),
            None => roots.push(i),
        }
        stack.push(i);
    }
    (kids, roots)
}

/// 折叠主入口: full 为文档序+depth 的扁平全量层(device::parse_dump 产物)。
pub fn fold(full: &[FullNode]) -> Vec<Line> {
    if full.is_empty() { return vec![]; }
    let (kids, roots) = tree_of(full);
    let mut out = Vec::new();
    for &r in &roots { emit(r, full, &kids, &mut out); }
    out
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::device::parse_dump;

    const FEED: &str = include_str!("testdata/feed.xml");
    const FEED2: &str = include_str!("testdata/feed2.xml");
    const SETTINGS: &str = include_str!("testdata/settings.xml");
    const MINE: &str = include_str!("testdata/mine.xml");

    fn lines_of(xml: &str) -> Vec<Line> {
        let (_, full, _) = parse_dump(xml);
        fold(&full)
    }

    #[test]
    fn feed_cards_fold_noise_gone_skeleton_intact() {
        // #18 案发现场: 信息流卡片折成头行,点赞数/作者互动钮全部收进折缝
        let ls = lines_of(FEED);
        let heads = ls.iter().filter(|l| l.kind == Kind::Head).count();
        assert!(heads >= 3, "至少3张卡片折叠,实际{heads}");
        for noise in ["赞", "328", "更多按钮", "小雅"] {
            assert!(!ls.iter().any(|l| l.kind == Kind::Plain && l.t == *noise),
                "卡片内部噪音'{noise}'不应平铺出现");
        }
        // 骨架一个不少: 频道栏7个Tab + 底部导航
        for tab in ["推荐", "热榜", "北京", "小说", "发现", "视频", "体育", "首页", "赚钱", "商城"] {
            assert!(ls.iter().any(|l| l.kind == Kind::Plain && l.t == *tab), "骨架Tab'{tab}'必须存活");
        }
        // 长文卡(2046字)必须折叠——字数维度判富,不靠条数
        assert!(ls.iter().any(|l| l.kind == Kind::Head && l.t.contains("2046字")),
            "长文卡按字数判富折叠");
        // 折后行数少于平铺文字行数
        let flat = parse_dump(FEED).1.iter().filter(|f| !f.t.is_empty()).count();
        assert!(ls.len() < flat, "折后{}行应少于平铺{}行", ls.len(), flat);
    }

    #[test]
    fn settings_menu_untouched() {
        // agent 主工作面: 菜单行全部低于富阈值,逐字保留,零折叠
        let (_, full, _) = parse_dump(SETTINGS);
        let ls = fold(&full);
        assert_eq!(ls.iter().filter(|l| l.kind == Kind::Head).count(), 0, "设置页不得折叠任何行");
        for f in full.iter().filter(|f| !f.t.is_empty()) {
            let want: String = f.t.chars().take(40).collect();
            assert!(ls.iter().any(|l| l.kind == Kind::Plain && l.t == want),
                "设置页文字'{want}'不得丢失");
        }
        assert_eq!(ls.iter().filter(|l| l.kind == Kind::List).count(), 1, "设置列表容器有一条注解");
    }

    #[test]
    fn mine_page_folds_cards_keeps_nav() {
        let ls = lines_of(MINE);
        assert!(ls.iter().filter(|l| l.kind == Kind::Head).count() >= 2);
        for tab in ["首页", "视频", "赚钱", "商城", "未登录"] {
            assert!(ls.iter().any(|l| l.kind == Kind::Plain && l.t == *tab), "导航'{tab}'必须存活");
        }
    }

    #[test]
    fn heterogeneous_feed_folds_via_scrollable_semantics() {
        // 局36实测破绽: 两张卡wrapper class不同(LinearLayout/FrameLayout),
        // 结构自相似(T2)兜不住 → 语义触发器(T1)按scrollable列表面逐子判富折叠
        let ls = lines_of(FEED2);
        assert!(ls.iter().filter(|l| l.kind == Kind::Head).count() >= 2,
            "异构wrapper的卡片也必须折叠");
        for noise in ["赞", "更多按钮", "13小时前"] {
            assert!(!ls.iter().any(|l| l.kind == Kind::Plain && l.t == *noise),
                "卡片内部噪音'{noise}'不应平铺出现");
        }
        assert!(ls.iter().any(|l| l.kind == Kind::Plain && l.t.contains("上次看到这里")),
            "分隔条是简单项,保持平铺");
        for tab in ["推荐", "热榜", "首页", "商城"] {
            assert!(ls.iter().any(|l| l.kind == Kind::Plain && l.t == *tab), "骨架'{tab}'必须存活");
        }
    }

    #[test]
    fn simple_repeats_never_fold() {
        // 频道栏: 同类Button×7但每个只有1条文字 → 简单项,永不折叠
        let ls = lines_of(FEED);
        let tabs = ["推荐", "热榜", "北京", "小说", "发现", "视频", "体育"];
        assert!(tabs.iter().all(|t| ls.iter().any(|l| l.kind == Kind::Plain && l.t == *t)));
    }

    #[test]
    fn head_is_largest_text_and_tap_anchor() {
        // 头行 = 子树内面积最大的文字,框指向该文字——折叠不藏交互把手
        let ls = lines_of(FEED);
        let h = ls.iter().find(|l| l.kind == Kind::Head).unwrap();
        assert!(h.t.starts_with("▸ "));
        assert!(area(h.b) > 0, "头行必须携带可点的真实框");
    }
}
