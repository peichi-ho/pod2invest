"""
seed_terms.py

功能：
- 初始化專有名詞資料庫
- 將預先整理好的常見財經 / 投資專有名詞
  （如 EPS、殖利率、本益比等）
  批次寫入 SQLite 資料庫

用途：
- 系統第一次執行時建立基本名詞庫
- demo 或重新建置環境時快速重建資料庫

備註：
- 本檔案不是系統主流程的一部分
- 屬於資料初始化（seed data）工具
"""

# seed_terms_stock_basics.py
from glossary_db import init_db, upsert_term_orm


def main():
    init_db()

    # 1) 面額
    upsert_term_orm(
        term="面額",
        aliases=["票面金額", "股票面額", "票面面額"],
        category="股票交易基礎",
        short_definition="股票票面所印的金額；台灣現行一般為每股 10 元。",
        long_definition=("指股票票面上所印製之金額。民國六十八年以前流通股票面值種類不一，"
                         "主管機關為便利交易與交割管理，於六十八年通令規定股票面值限期統一改為十元，"
                         "即一般所稱現行股票面額。"),
        lang="zh-TW",
    )

    # 2) 面值
    upsert_term_orm(
        term="面值",
        aliases=["每股面值"],
        category="股票交易基礎",
        short_definition="股票上印刷的每股金額（票面上標示的每股金額）。",
        long_definition="指股票上所印刷之每股帳面值每股金額。",
        lang="zh-TW",
    )

    # 3) 市價
    upsert_term_orm(
        term="市價",
        aliases=["市場價格", "股價", "現價"],
        category="股票交易基礎",
        short_definition="由市場交易決定、隨時變動的股票價格。",
        long_definition=("股票的市價是經由交易決定的價錢，隨時都在變動。"
                         "影響因素包括發行公司獲利能力、市場資金供需關係，"
                         "甚至人為炒作等，皆可能影響股票的市價。"),
        lang="zh-TW",
    )

    # 4) 淨值
    upsert_term_orm(
        term="淨值",
        aliases=["每股淨值", "帳面價值", "BV", "Book Value"],
        category="財報指標",
        short_definition="股票的帳面價值；常用「每股淨值」表示。",
        long_definition=("股票淨值即股票的帳面價值。通常以公司的資本額加上法定公積、"
                         "資本公積及累積盈餘（或減除虧損）得到淨值總額；"
                         "再以淨值總額除以發行股份總數，可得每股淨值。"),
        lang="zh-TW",
    )

    # 5) 毛利率
    upsert_term_orm(
        term="毛利率",
        aliases=["Gross Margin", "GM"],
        category="財報指標",
        short_definition="衡量公司產品附加價值與獲利能力的指標。",
        long_definition=("毛利率用以衡量公司的產品價值與附加價值創造能力。"
                         "一般定義為：（銷售收入－銷售成本）／銷售收入。"),
        lang="zh-TW",
    )

    # 6) 最高價
    upsert_term_orm(
        term="最高價",
        aliases=["當日最高價", "High"],
        category="行情與成交",
        short_definition="當天成交價格中最高的價格。",
        long_definition=("行情表中所指的最高，是當天成交的各種不同價格中之最高價格；"
                         "最高價的成交筆數可能是一筆，也可能不只一筆。"),
        lang="zh-TW",
    )

    # 7) 最低價
    upsert_term_orm(
        term="最低價",
        aliases=["當日最低價", "Low"],
        category="行情與成交",
        short_definition="當天成交價格中最低的價格。",
        long_definition="當天成交價格中最低的價格。",
        lang="zh-TW",
    )

    # 8) 成交價
    upsert_term_orm(
        term="成交價",
        aliases=["撮合價", "Trade Price", "Last"],
        category="行情與成交",
        short_definition="買賣雙方經撮合成交時的價格。",
        long_definition=("指買方欲進股票的價位與賣方賣出股票價位相同，"
                         "經電腦撮合而成交時的價位。"),
        lang="zh-TW",
    )

    # 9) 成交值
    upsert_term_orm(
        term="成交值",
        aliases=["成交金額", "Turnover"],
        category="行情與成交",
        short_definition="成交量乘以成交價所得到的金額。",
        long_definition="成交量乘上股價等於成交值。",
        lang="zh-TW",
    )
    # 成交量
    upsert_term_orm(
        term="成交量",
        aliases=["交易量", "Volume"],
        category="行情與成交",
        short_definition="某檔股票/債券在交易日實際成交的股數（或張數）。",
        long_definition="某一檔股票或債券憑證在交易日所成交的股數。",
        lang="zh-TW",
    )

    # 漲跌停板
    upsert_term_orm(
        term="漲跌停板",
        aliases=["漲停", "跌停", "Price Limit"],
        category="交易制度",
        short_definition="股價單日漲跌幅達規定上限（台股多為±10%）即停止再漲跌的限制。",
        long_definition=(
            "根據台灣證券交易所的規定，凡是股票升降幅度超過前一營業日收盤價格的10%，"
            "股價將停止升降，而這個10%的限度就稱為漲停板或跌停板。漲、跌停板的主要用意"
            "是限制股價過度波動或投機，因此一旦達到10%時，交易所會顯示漲停板或跌停板。"
        ),
        lang="zh-TW",
    )

    # 除息
    upsert_term_orm(
        term="除息",
        aliases=["除息日", "Ex-dividend", "Ex-Dividend"],
        category="除權息與配息",
        short_definition="股息權利切割的交易：在停止過戶前持有者可領取股息。",
        long_definition=("在股票停止過戶前買進股票的投資人，因為股東名簿上登記，所以能領取各公司所發放的股息。"),
        lang="zh-TW",
    )

    # 除權
    upsert_term_orm(
        term="除權",
        aliases=["除權日", "Ex-rights", "Ex-Rights"],
        category="除權息與配息",
        short_definition="股票股利權利切割的交易；除權基準日前買進者可享有配股/增資。",
        long_definition=("分配股票股利的交易。上市公司分配股票股利時都訂有一個「除權基準日」，"
                         "該日以前買進該股才可享受增資股分配。"),
        lang="zh-TW",
    )

    # 填息
    upsert_term_orm(
        term="填息",
        aliases=["填息行情", "填息完成"],
        category="除權息與配息",
        short_definition="除息後股價上漲，回補除息前收盤價與除息價之間的缺口。",
        long_definition=("除息交易前一日該股的收盤價與除息價間留下一個除息價位缺口，"
                         "如果除息後股價上升將該價位缺口填滿。"),
        lang="zh-TW",
    )

    # 填權
    upsert_term_orm(
        term="填權",
        aliases=["填權行情", "填權完成"],
        category="除權息與配息",
        short_definition="除權後股價上漲，回補除權前收盤價與除權後理論價之間的缺口。",
        long_definition=("除權交易前一日該股的收盤價與除權後價位間留下一個除權價位缺口，"
                         "如果除權後股價上升將該價位缺口填滿。"),
        lang="zh-TW",
    )

    # 技術分析
    upsert_term_orm(
        term="技術分析",
        aliases=["技術面分析", "Technical Analysis"],
        category="投資方法",
        short_definition="以過去市場價格/量等資料（常用圖表）推估趨勢並制定策略的方法。",
        long_definition=("指研究過去金融市場的資訊（主要是經由使用圖表）來預測價格的趨勢與決定投資的策略。"),
        lang="zh-TW",
    )

    # 毛利
    upsert_term_orm(
        term="毛利",
        aliases=["營業毛利", "Gross Profit"],
        category="財報指標",
        short_definition="營業收入扣除銷貨成本後的利益（衡量本業獲利能力）。",
        long_definition=("營業毛利＝營業收入淨額－營業成本（銷貨成本），是衡量企業盈利趨勢的主要指標。"),
        lang="zh-TW",
    )

    # 被動投資
    upsert_term_orm(
        term="被動投資",
        aliases=["被動式投資", "Passive Investing"],
        category="投資方法",
        short_definition="不做選股與時機預測，長期持有整體市場以取得接近市場報酬。",
        long_definition=("不做任何研究和預測，一次持有整體市場上的所有投資標的，"
                         "得到跟整體市場長期經濟成長一樣的成果。"),
        lang="zh-TW",
    )

    # 主動投資
    upsert_term_orm(
        term="主動投資",
        aliases=["主動式投資", "Active Investing"],
        category="投資方法",
        short_definition="透過選股與選時，目標取得優於市場指數的績效。",
        long_definition=("透過「選擇標的」和「選擇時機」，希望創造比整體市場加權平均（指數）更好的績效。"),
        lang="zh-TW",
    )

    # 基金
    upsert_term_orm(
        term="基金",
        aliases=["共同基金", "Fund", "Mutual Fund"],
        category="投資工具",
        short_definition="集合眾人資金，由專業經理人集中管理投資的工具。",
        long_definition="集合一群人的資金，由經理人集中管理投資，創造更高的投資報酬。",
        lang="zh-TW",
    )

    # 指數型基金
    upsert_term_orm(
        term="指數型基金",
        aliases=["Index Fund", "指數基金"],
        category="投資工具",
        short_definition="追蹤特定市場指數組成配置的共同基金，通常費用較低。",
        long_definition=("為開放式的共同基金，這類基金會選定某個市場指數，按照該指數組成來購買證券，"
                         "因此持有標的完全依據指數而決定，不需依靠經理人的主觀判斷，所以管理費通常較主動型基金低。"),
        lang="zh-TW",
    )

    # ETF
    upsert_term_orm(
        term="ETF",
        aliases=[
            "exchange traded fund",
            "Exchange Traded Fund",
            "交易所買賣基金",
            "指數股票型基金",
            "股票型指數基金",
        ],
        category="投資工具",
        short_definition="可在交易所像股票買賣、通常被動追蹤指數表現的基金。",
        long_definition=(
            "稱為股票型指數基金、指數股票型基金、交易所買賣基金，翻譯上都是指能在股票交易所買賣的指數型基金。"
            "換句話說，ETF就是被動追蹤某一指數表現的共同基金，並且在集中市場掛牌，像一般股票交易讓投資人買賣。"
        ),
        lang="zh-TW",
    )

    # 期貨
    upsert_term_orm(
        term="期貨",
        aliases=["futures", "Futures"],
        category="衍生性商品",
        short_definition="買賣雙方約定未來時間以特定價格交割標的物的合約交易。",
        long_definition=("是一種跨越時間的交易方式。買賣雙方透過簽訂合約，同意按指定的時間、價格與其他交易條件，"
                         "交收指定數量的現貨。"),
        lang="zh-TW",
    )

    # 債券
    upsert_term_orm(
        term="債券",
        aliases=["Bond", "Bonds"],
        category="投資工具",
        short_definition="發行人借款給投資人並承諾付息、到期還本的有價憑證。",
        long_definition=("是政府、金融機構、工商企業等機構向社會借債籌措資金時，向投資者發行，"
                         "承諾按一定利率支付利息並按約定條件償還本金的債權債務憑證。"),
        lang="zh-TW",
    )

    # 證券
    upsert_term_orm(
        term="證券",
        aliases=["有價證券", "Securities"],
        category="投資工具",
        short_definition="表示財產權（如股權/債權）的有價憑證，例如股票、債券等。",
        long_definition=("為有價證券的簡稱，是一種表示財產權的有價憑證。持有者可以依據此憑證，"
                         "證明其所有權或債權等私權的證明文件，例如：股票、債券、權證和股票價款繳納憑證等。"),
        lang="zh-TW",
    )

    # 選擇權
    upsert_term_orm(
        term="選擇權",
        aliases=["期權", "Options", "Option"],
        category="衍生性商品",
        short_definition="可交易的權利：依契約於到期日前/日以約定條件買賣標的物。",
        long_definition=("選擇權是一種可以交易的權利，買、賣雙方簽訂契約，決議到期日、標的物、履約價格以及買賣數量。"),
        lang="zh-TW",
    )

    # 創投
    upsert_term_orm(
        term="創投",
        aliases=["venture capital", "Venture Capital", "創業投資"],
        category="投資工具",
        short_definition="以高風險/高成長投資案為主，協助企業成長並追求高回收的投資基金。",
        long_definition=("創業投資，指由一群具有技術、財務、市場或產業專業知識和經驗的人士操作，"
                         "以其專業能力協助投資人於高風險、高成長的投資案中，選擇並投資有潛力之企業，"
                         "追求未來高回收報酬的基金。"),
        lang="zh-TW",
    )
    # 私募基金（private equity）
    upsert_term_orm(
        term="私募基金",
        aliases=["private equity", "Private Equity", "PE基金"],
        category="投資工具",
        short_definition="向少數特定投資者非公開募集資金的基金，通常不在公開市場自由交易。",
        long_definition=(
            "針對少數投資者而私下（非公開）地募集資金併成立運作的投資基金，不能在股票市場上自由交易，"
            "因此又被稱為向特定對象募集的基金或「地下基金」。其方式基本有兩種："
            "一是基於簽訂委托投資合同的契約型集合投資基金，二是基於共同出資入股成立股份公司的公司型集合投資基金。"
        ),
        lang="zh-TW",
    )

    # 避險基金
    upsert_term_orm(
        term="避險基金",
        aliases=["對沖基金", "套利基金", "Hedge Fund", "hedging", "Hedging"],
        category="投資工具",
        short_definition="常運用衍生性工具與多種策略，以追求獲利並管理風險的基金。",
        long_definition=("又稱對沖基金或套利基金，是指由金融期貨、金融期權等金融衍生工具與金融組織結合後，"
                         "以盈利為目的的金融基金。其最初目的為透過套期保值（hedging）避免損失。"),
        lang="zh-TW",
    )

    # 共同基金
    upsert_term_orm(
        term="共同基金",
        aliases=["Mutual Fund", "共同基金(公募)", "公募基金"],
        category="投資工具",
        short_definition="向社會投資者公開募集資金，由專業經理人投資於證券市場的基金。",
        long_definition=("是由基金經理的專業金融從業者管理，向社會投資者公開募集資金以投資於證券市場的"
                         "營利性的公司型證券共同基金。"),
        lang="zh-TW",
    )

    # 股票型基金
    upsert_term_orm(
        term="股票型基金",
        aliases=["Equity Fund"],
        category="投資工具",
        short_definition="主要投資標的為國內外企業公開發行股票的基金。",
        long_definition="資金主要集中投資的標的為國內外企業公開發行的股票。",
        lang="zh-TW",
    )

    # 科技股
    upsert_term_orm(
        term="科技股",
        aliases=["科技類股", "Technology Stocks", "Tech Stocks"],
        category="市場與產業",
        short_definition="產品/服務具高技術含量、在科技產業領先企業的股票。",
        long_definition=(
            "指那些產品和服務具有高技術含量，在行業領域領先的企業的股票。比如："
            "從事電信服務、電信設備製造、電腦軟硬體、新材料、新能源、航天航空、有線數字電視、"
            "生物醫藥製品等服務與生產的公司通稱為科技行業。"
        ),
        lang="zh-TW",
    )

    # 股票市場
    upsert_term_orm(
        term="股票市場",
        aliases=["股市", "Stock Market"],
        category="市場與產業",
        short_definition="股票發行、買賣與交易的市場，是證券市場的一部分。",
        long_definition="指股票發行、買賣、交易的市場，是證券市場的一部分。",
        lang="zh-TW",
    )

    # 總體經濟
    upsert_term_orm(
        term="總體經濟",
        aliases=["宏觀經濟", "Macroeconomics"],
        category="總體與景氣",
        short_definition="以國民收入、投資、消費等總體指標分析經濟運行規律的領域。",
        long_definition="是指用國民收入、經濟整體的投資和消費等總體性的統計概念來分析經濟運行規律的一個經濟學領域。",
        lang="zh-TW",
    )

    # 景氣循環
    upsert_term_orm(
        term="景氣循環",
        aliases=["經濟循環", "經濟週期", "商業週期", "Business Cycle"],
        category="總體與景氣",
        short_definition="經濟在長期趨勢附近上下波動，包含擴張/繁榮與收縮/衰退的交替。",
        long_definition=(
            "又稱經濟循環、經濟週期、商業週期，是國內生產總值（GDP）在其長期增長趨勢附近的上下移動。"
            "景氣循環的長度是指包含一次繁榮和收縮的時間段。這些波動通常包含相對快速的經濟增長時期（擴張或繁榮）"
            "和相對停滯或下降時期（收縮或衰退）之間的變化。"
        ),
        lang="zh-TW",
    )

    # 價值投資
    upsert_term_orm(
        term="價值投資",
        aliases=["Value Investing", "Benjamin Graham", "班傑明·葛拉漢", "智慧型股票投資人", "證券分析"],
        category="投資方法",
        short_definition="以「內在價值」為核心，尋找價格低於價值的公司以降低風險並追求報酬。",
        long_definition=(
            "一種投資策略，主要由班傑明‧葛拉漢（Benjamin Graham）在《智慧型股票投資人》與《證券分析》中建立，"
            "他也因此被稱為價值投資之父。其核心是每間公司都有潛在的內在價值，但股價常與內在價值偏離；"
            "因此投資價格低於內在價值的穩健公司，可能以較低風險帶來不錯報酬。"
        ),
        lang="zh-TW",
    )

    # 經濟利潤
    upsert_term_orm(
        term="經濟利潤",
        aliases=["Economic Profit"],
        category="總體與景氣",
        short_definition="總收入與總成本之間的差額。",
        long_definition="總收入和總成本之間的差額。",
        lang="zh-TW",
    )

    # 散戶
    upsert_term_orm(
        term="散戶",
        aliases=["個人投資人", "Retail Investor"],
        category="市場參與者",
        short_definition="在券商開戶、以個人名義買賣股票的自然人投資者。",
        long_definition="指的是在券商開戶買賣股票的個別自然人。",
        lang="zh-TW",
    )

    # 機構投資人
    upsert_term_orm(
        term="機構投資人",
        aliases=["法人", "Institutional Investor"],
        category="市場參與者",
        short_definition="以自有或受託資產進行投資的組織，如退休基金、保險公司、銀行等。",
        long_definition=("以自身的資產或信托資產進行投資的組織，如退休基金、投資公司、保險公司、銀行、信托基金、慈善基金等機構。"),
        lang="zh-TW",
    )

    # 金融市場
    upsert_term_orm(
        term="金融市場",
        aliases=["Financial Market"],
        category="市場與產業",
        short_definition="資金融通與有價證券交易的場所/機制，可為實體或電子化交易環境。",
        long_definition=(
            "具有一定規模的資金融通、貨幣借貸和買賣有價證券的活動和場所。金融市場不一定要在固定場所中，"
            "通過電子通訊等方式完成的交易也可視為金融市場的一部分。參與者是資金供求雙方，包括個人、企業、銀行、"
            "經紀人、證券公司、保險公司、投資機構及政府機構等。交易對象是貨幣形態的資金商品，並以利息作為價格。"
            "利息通常是資金使用權轉移的代價或資金參與生成利潤的分割。"
        ),
        lang="zh-TW",
    )

    # 投資組合
    upsert_term_orm(
        term="投資組合",
        aliases=["Portfolio", "資產配置"],
        category="投資方法",
        short_definition="透過配置多種相關性較低的資產以分散風險、穩定資產價值。",
        long_definition=("藉由投資不同類型、關聯性低的資產（股票、債券、外幣、期權、貴金屬、金融衍生工具、房地產、土地、"
                         "古董、藝術品等），達到分散風險與穩定資產價值的效果，並掌握各種資產的變化表現。"),
        lang="zh-TW",
    )

    # 基本面
    upsert_term_orm(
        term="基本面",
        aliases=["Fundamentals", "基本面分析"],
        category="投資方法",
        short_definition="對宏觀經濟、產業與公司經營/財務狀況等的分析。",
        long_definition="對宏觀經濟、行業和公司基本情況的分析，包括公司經營理念、財務報表等分析。",
        lang="zh-TW",
    )
    # 基本面分析

    upsert_term_orm(
        term="基本面分析",
        aliases=["Fundamental Analysis", "Fundamentals Analysis"],
        category="投資方法",
        short_definition="立足財務報表與公司經營/產業前景，估算股票價值並判斷是否值得投資。",
        long_definition=(
            "注重研究公司的本質，立足於財務報表從而對公司的經營情況進行深入了解，同時估出公司股票的價值。"
            "投資者經過對公司的經營狀況，如公司負債、利潤率、回報率來分析此公司股票是否誘人，是否值得投資者去投資，是否會得到高額回報。"
            "同時市場對這個行業的前景是否支持，以及公司管理層的整體素質都是影響投資者判斷公司價值的重要因素。"
        ),
        lang="zh-TW",
    )

    # 風險趨避
    upsert_term_orm(
        term="風險趨避",
        aliases=["風險厭惡", "Risk Aversion"],
        category="投資與風險",
        short_definition="面對不確定收益時偏好較安全但期望報酬較低的選擇；相對概念為風險容忍。",
        long_definition=(
            "指一個人面對不確定收益的交易時，更傾向於選擇較保險但是也可能具有較低期望收益的交易。"
            "例如一個風險厭惡的投資者，會選擇將他的錢存在銀行以獲得較低但確定的利息，而不願意將錢用於購買股票，"
            "承擔損失的風險以獲得較高的期望收益。與風險厭惡程度相對的有「風險容忍」。"
        ),
        lang="zh-TW",
    )

    # 不完美市場
    upsert_term_orm(
        term="不完美市場",
        aliases=["Imperfect Market"],
        category="市場結構",
        short_definition="非完全競爭的市場型態，例如壟斷競爭、寡頭壟斷與完全壟斷。",
        long_definition="不完美市場包括壟斷競爭、寡頭壟斷和完全壟斷。",
        lang="zh-TW",
    )

    # 同質產品
    upsert_term_orm(
        term="同質產品",
        aliases=["同質性產品", "Homogeneous Product"],
        category="市場結構",
        short_definition="消費者感受在性能/特點等方面非常相似、差異很小的產品。",
        long_definition="指消費者所感覺的產品在性能、特點等方面非常相似。",
        lang="zh-TW",
    )

    # 基金經理人
    upsert_term_orm(
        term="基金經理人",
        aliases=["Fund Manager", "Portfolio Manager"],
        category="市場參與者",
        short_definition="負責管理基金投資組合與資產運用，制定並執行投資策略與決策的人。",
        long_definition=("指管理基金投資組合之人，主要負責基金資產管理運用，包括：投資決策與策略之制定及實施。"),
        lang="zh-TW",
    )

    # IPO
    upsert_term_orm(
        term="IPO",
        aliases=["initial public offerings", "Initial Public Offering", "首次公開發行", "首次公開募股"],
        category="市場制度",
        short_definition="公司首次向大眾公開發行股票並上市（或進入公開市場）募資的過程。",
        long_definition=("公司第一次從私人公司，變成上市公司。「首次公開發行」或「首次公開募股」。"),
        lang="zh-TW",
    )

    # 上市公司
    upsert_term_orm(
        term="上市公司",
        aliases=["公開發行公司", "Listed Company"],
        category="市場制度",
        short_definition="其股票或公司債等可在證券交易所公開交易的股份有限公司。",
        long_definition="指可以在證券交易所公開交易其公司股票、公司債等的股份有限公司。",
        lang="zh-TW",
    )

    # 資金成本
    upsert_term_orm(
        term="資金成本",
        aliases=["資本成本", "Capital Cost", "Cost of Capital"],
        category="公司財務",
        short_definition="資金投入專案所要求的預期回報；對投資人是機會成本，對募資者是籌資代價。",
        long_definition=(
            "是指市場為將資金引入某個投資項目而所要求的預期回報。對於投資者，一個投資項目的資本成本是一種機會成本，"
            "即投資者為選擇此項目而放棄了其他項目所付出的代價。另一方面，尋求投資的項目方，例如公司，為了說服投資者去承擔這個代價，"
            "需要承諾一定的預期回報，這個承諾性的回報，即是項目方為了籌集資金而承擔的資本成本。"
        ),
        lang="zh-TW",
    )

    # 折現率
    upsert_term_orm(
        term="折現率",
        aliases=["Discount Rate"],
        category="公司財務",
        short_definition="把未來現金流折算為現在價值的利率；折現率越高，現值越低。",
        long_definition=(
            "將「未來」一系列現金流量折算成目前價值的利率，即為了計算未來一特定金額於現在的價值時，"
            "可利用「適當的利率」而折現，以求得當前之現值，而此「適當的利率」稱為折現率。"
            "就數學關係而言，折現率越高，代表「未來」一系列現金流量的現值越低，亦即折現率與價值成反比。"
        ),
        lang="zh-TW",
    )

    # 現金流量
    upsert_term_orm(
        term="現金流量",
        aliases=["Cash Flow", "現金流"],
        category="財報指標",
        short_definition="一定期間企業現金流入/流出的總量情況（含營運、投資、籌資等活動）。",
        long_definition=(
            "是指企業在一定會計期間按照現金收付實現制，通過一定經濟活動（包括經營活動、投資活動、籌資活動和非經常性項目)"
            "而產生的現金流入、現金流出及其總量情況的總稱。即企業一定時期的現金和現金等價物的流入和流出的數量。"
        ),
        lang="zh-TW",
    )

    # 自由市場
    upsert_term_orm(
        term="自由市場",
        aliases=["Free Market"],
        category="市場結構",
        short_definition="金錢與貨物流動主要由市場供需自然運作，政府不介入控制的市場。",
        long_definition="指金錢、貨物的流動完全是根據市場自然的狀況而進行的，政府不介入控制。",
        lang="zh-TW",
    )

    # 次級市場
    upsert_term_orm(
        term="次級市場",
        aliases=["Secondary Market"],
        category="市場制度",
        short_definition="有價證券在初次發行後，投資人之間進行買賣交易的市場。",
        long_definition="次級市場係指初級市場發行後之有價證券買賣之交易市場。",
        lang="zh-TW",
    )

    # 初級市場
    upsert_term_orm(
        term="初級市場",
        aliases=["Primary Market"],
        category="市場制度",
        short_definition="資金需求者首次出售有價證券給最初購買者以籌資的市場。",
        long_definition="指資金需求者（包括政府單位、金融機構及公民營企業）為籌集資金首次出售有價證券予最初購買者之交易市場。",
        lang="zh-TW",
    )

    # 完全競爭市場
    upsert_term_orm(
        term="完全競爭市場",
        aliases=["Perfect Competition"],
        category="市場結構",
        short_definition="買賣方眾多、資訊充分、產品同質、進出障礙低，廠商為價格接受者的市場。",
        long_definition=(
            "市場參與者之買賣雙方數量眾多、有完全訊息、交易的商品具同質性、廠商進出市場幾無障礙，而為價格接受者。"
            "每一需求者的購買量與供給者的生產量在整體市場中所占比例極低，均無決定性影響力，完全依市場機能運行。"
        ),
        lang="zh-TW",
    )

    # 異質市場
    upsert_term_orm(
        term="異質市場",
        aliases=["Heterogeneous Market", "產品差異化市場"],
        category="市場結構",
        short_definition="交易商品彼此不同、存在差異化競爭的市場（相對同質市場）。",
        long_definition=("指賣不同商品的市場。異質市場的異質不是指質量不同，一般而言異質市場上的競爭要比同質市場上的小。"),
        lang="zh-TW",
    )

    # 流動性
    upsert_term_orm(
        term="流動性",
        aliases=["Liquidity"],
        category="市場與資產特性",
        short_definition="資產能以合理價格順利變現（快速賣出換現）的能力。",
        long_definition="指資產能夠以一個合理的價格順利變現的能力。",
        lang="zh-TW",
    )

    # 錯價（mispricing）
    upsert_term_orm(
        term="錯價",
        aliases=["mispricing", "Mispricing", "高估", "低估"],
        category="市場與資產特性",
        short_definition="資產價格未正確反映其真實價值，長期被高估或低估的現象。",
        long_definition="某些資產（如股票）持續被投資人低（高）估或未正確反應其真實價格。",
        lang="zh-TW",
    )

    # 股利
    upsert_term_orm(
        term="股利",
        aliases=["股息", "紅利", "Dividend"],
        category="除權息與配息",
        short_definition="公司將部分收益分配給股東作為持股報酬的現金或其他形式給付。",
        long_definition="又稱股息或紅利，是指股份公司將部分收益派發給股東，作為股東持有股票、提供資本的報酬。",
        lang="zh-TW",
    )

    # 要求報酬率
    upsert_term_orm(
        term="要求報酬率",
        aliases=["必要報酬率", "Required Return", "Hurdle Rate"],
        category="投資與風險",
        short_definition="投資人期望投資至少要達到的最低報酬率（低於此可能選擇其他用途）。",
        long_definition=(
            "又稱必要報酬率，是指投資人期望一項投資應提供的最低報酬率。"
            "例如某投資人購買股票的要求報酬率是10%，若同時有利率為10%的房貸在還款中，"
            "就可能認為若股票投資報酬率不足10%，不如將錢用來償還房貸。"
        ),
        lang="zh-TW",
    )

    # 殖利率（更新/補充）
    upsert_term_orm(
        term="殖利率",
        aliases=["股息殖利率", "股利殖利率", "Dividend Yield"],
        category="除權息與配息",
        short_definition="每股股息（現金股利）÷ 每股股價，通常以百分比表示。",
        long_definition="是每股股息（現金股利）除以每股股價，通常以百分比表示。",
        lang="zh-TW",
    )

    # 經理費／管理費
    upsert_term_orm(
        term="經理費／管理費",
        aliases=["經理費", "管理費", "Management Fee"],
        category="基金費用",
        short_definition="支付給基金公司作為研究與管理資產的費用。",
        long_definition="是付給基金公司，作為基金公司研究團隊幫你管理資產的費用。",
        lang="zh-TW",
    )

    # 保管費
    upsert_term_orm(
        term="保管費",
        aliases=["Custody Fee", "託管費"],
        category="基金費用",
        short_definition="支付給保管銀行的費用，用於基金資產託管與保管服務。",
        long_definition=("付給保管銀行的費用。因為基金公司旗下所管理的每一檔基金，都各自是由第三方的保管銀行所保管，"
                         "所以保管銀行會收取保管費。"),
        lang="zh-TW",
    )

    # 聯邦準備理事會（The Fed）
    upsert_term_orm(
        term="聯邦準備理事會",
        aliases=["聯準會", "The Federal Reserve System", "The Fed", "Federal Reserve"],
        category="總體與機構",
        short_definition="美國中央銀行體系（聯準會），負責貨幣政策等。",
        long_definition="簡稱為聯準會，是美國的中央銀行。",
        lang="zh-TW",
    )

    # 資本資產定價模型（CAPM）
    upsert_term_orm(
        term="資本資產定價模型",
        aliases=["capital asset pricing model", "CAPM", "Capital Asset Pricing Model"],
        category="金融理論",
        short_definition="用來解釋風險與預期報酬關係，進而推導股票等資產必要報酬率的模型。",
        long_definition=("資本資產主要指的是股票資產，而定價則試圖解釋資本市場如何決定股票收益率，進而決定股票價格。"),
        lang="zh-TW",
    )

    # 資本市場
    upsert_term_orm(
        term="資本市場",
        aliases=["中長期資金市場", "Capital Market"],
        category="市場制度",
        short_definition="以中長期資金借貸與證券融資/交易為主的市場。",
        long_definition="又稱（中）長期資金市場，是指證券融資和經營一年以上的資金借貸和證券交易的場所。",
        lang="zh-TW",
    )

    # 盈餘慣性（PEAD）
    upsert_term_orm(
        term="盈餘慣性",
        aliases=["post-earnings-announcement drift", "PEAD"],
        category="市場異象",
        short_definition="財報公布後，股價超額報酬仍可能持續數週到數月向同方向延續的現象。",
        long_definition=(
            "是一種在財務公布後的數周甚至數月內，仍然向超額回報方向連續獲取超額收益的趨勢。"
            "通常認為財務現狀公布後資訊應很快被投資者消化並反映在價格中，但實際情況並非如此。"
            "對於公布較高季度利潤的公司，其超額資產回報傾向於在公布盈利額度後向該方向再「漂移」至少六十天；"
            "類似地，報告較差的公司也傾向於向不利方向漂移同樣長的時間。"
        ),
        lang="zh-TW",
    )
    # 基點（basic points，BPS）
    upsert_term_orm(
        term="基點",
        aliases=["basic points", "basis points", "BPS", "bp", "Basis Point"],
        category="利率與報酬",
        short_definition="利率變動的最小常用單位；1 基點 = 0.01%（= 0.0001）。",
        long_definition="指衡量債券或期票利率變動的最小計量單位，1個基點等於0.01％，即1％的百分之一。",
        lang="zh-TW",
    )

    # 財務報表
    upsert_term_orm(
        term="財務報表",
        aliases=["財報", "Financial Statements"],
        category="會計與財報",
        short_definition="反映企業一定期間（季/年）財務表現與期末狀況的會計文件。",
        long_definition=(
            "簡稱財報，是一套會計文件，它反映一家企業過去一個財政時間段（主要是季度或年度）的財政表現及期末狀況。"
            "它以量化的財務數字分目表達，能幫助投資者和債權人了解企業經營狀況，進一步協助經濟決策。"
        ),
        lang="zh-TW",
    )

    # 投資組合權數
    upsert_term_orm(
        term="投資組合權數",
        aliases=["權重", "Portfolio Weight", "Weight"],
        category="投資方法",
        short_definition="單一投資項目在整體投資組合價值中所占的比例（百分比）。",
        long_definition="每一投資項目佔總投資組合價值的百份比。",
        lang="zh-TW",
    )

    # 隱含報酬率（IRR）
    upsert_term_orm(
        term="隱含報酬率",
        aliases=["內含報酬率", "內部報酬率", "internal rate of return", "IRR"],
        category="公司財務",
        short_definition="使投資案淨現值（NPV）= 0 的折現率；等價於現金流入現值=流出現值的利率。",
        long_definition=("又稱內含報酬率、內部報酬率，能夠使未來現金流入量現值等於未來現金流出量現值的折現率，"
                         "或者說是使投資方案凈現值為0的折現率。"),
        lang="zh-TW",
    )

    # 股票專有名詞
    upsert_term_orm(
        term="股票專有名詞",
        aliases=["股市術語", "股票術語"],
        category="股票交易基礎",
        short_definition="股票交易中常用的專門術語與名詞集合（如成交量、融資融券、買超賣超等）。",
        long_definition="交易時用到的股市術語。",
        lang="zh-TW",
    )

    # 存股（票）
    upsert_term_orm(
        term="存股",
        aliases=["存股(票)", "長期持有", "Buy and Hold"],
        category="投資方法",
        short_definition="買進股票後長期持有、不頻繁交易，以領股利或長期增值為主的策略。",
        long_definition="買進股票後，長期持有數年不賣出，以每年領股利為主的一種股票投資法。",
        lang="zh-TW",
    )

    # 分散風險
    upsert_term_orm(
        term="分散風險",
        aliases=["分散投資", "Diversification"],
        category="投資方法",
        short_definition="把資金分配到多種相關性較低的資產，以降低單一風險對整體的影響。",
        long_definition=("在證券投資上，是指將資金分配在多種資產上，而這些資產的回報率相互之間的關聯性比較低，以達分散風險的目的。"
                         "這樣做既可以降低風險，又不會損及收益。"),
        lang="zh-TW",
    )

    # 融資
    upsert_term_orm(
        term="融資",
        aliases=["融資買進", "Margin Buying", "Margin"],
        category="信用交易",
        short_definition="向券商借款放大買股部位的交易方式；融資餘額變化常被用來觀察市場籌碼結構。",
        long_definition=(
            "指散戶擴張信用之做法。意即手上錢不多又想多買些股票，此時可向證券公司申請融資，而達到融資買入的目的。"
            "因此特別注意：當融資餘額不斷增加時，表示大部份的股票已經由大戶手中轉入散戶手中，後市行情下跌。"
        ),
        lang="zh-TW",
    )

    # 融券（墊股）
    upsert_term_orm(
        term="融券",
        aliases=["融券(墊股)", "融券賣出", "Short Selling", "Securities Lending"],
        category="信用交易",
        short_definition="向證券金融公司借股票先賣出，之後再買回回補以完成還券的交易方式。",
        long_definition=("手上沒有股票且同時看壞後市，先向證券金融公司借股票在證券市場賣出謂之融券賣出。"
                         "以後不管漲跌，將這些股票回補時謂之融券買入。"),
        lang="zh-TW",
    )

    # 解套
    upsert_term_orm(
        term="解套",
        aliases=["解套價", "回本", "Break-even"],
        category="投資行為與情緒",
        short_definition="套牢後股價回升至原買進價附近（或回本）而得以賣出離場。",
        long_definition="買入股票套牢後，等股票回升至原來買進價位。",
        lang="zh-TW",
    )

    # 買超
    upsert_term_orm(
        term="買超",
        aliases=["Net Buy", "淨買入"],
        category="行情與成交",
        short_definition="買進的數量或金額大於賣出的數量或金額。",
        long_definition="買進的數量或金額，超過賣出的數量或金額。",
        lang="zh-TW",
    )

    # 賣超
    upsert_term_orm(
        term="賣超",
        aliases=["Net Sell", "淨賣出"],
        category="行情與成交",
        short_definition="賣出的數量或金額大於買進的數量或金額。",
        long_definition="賣出的數量或金額，超過買進的數量或金額。",
        lang="zh-TW",
    )

    # 利率
    upsert_term_orm(
        term="利率",
        aliases=["利息率", "Interest Rate"],
        category="利率與報酬",
        short_definition="借款成本或放款報酬，通常以一年期利息與本金的百分比表示。",
        long_definition=(
            "又稱利息率，是借款的人需要向其所借的金錢所支付的代價（如貸款利率），"
            "抑或是放款的人延遲其消費，借給借款人所獲得的報酬（如銀行定存利率）。"
            "利率通常以一年期利息與本金的百分比計算。"
        ),
        lang="zh-TW",
    )

    # 股票價格
    upsert_term_orm(
        term="股票價格",
        aliases=["股價", "Stock Price", "Price"],
        category="股票交易基礎",
        short_definition="股票在證券市場上買賣時形成的價格（可視為市場交易決定）。",
        long_definition="股票可以當作商品出賣，並且有一定的價格，是指股票在證券市場上買賣的價格。",
        lang="zh-TW",
    )

    # 籌碼
    upsert_term_orm(
        term="籌碼",
        aliases=["籌碼面", "持股", "Shares"],
        category="投資方法",
        short_definition="在股市語境中泛指股票持有量/持股結構（籌碼分布）。",
        long_definition="在股票市場中，籌碼就是股票。",
        lang="zh-TW",
    )

    # 炒作
    upsert_term_orm(
        term="炒作",
        aliases=["炒股", "Speculation", "Market Hype"],
        category="投資行為與情緒",
        short_definition="透過渲染消息或操作資金等方式推升/壓低價格以達獲利或宣傳效果的行為。",
        long_definition=("是指商家或媒體從自身利益出發對某一新聞事件進行大規模炒作，加大渲染力度，"
                         "以達到廣告宣傳或正常新聞宣傳難以達到的商業效果。"),
        lang="zh-TW",
    )

    # 利多
    upsert_term_orm(
        term="利多",
        aliases=["好消息", "Positive News", "Bullish News"],
        category="投資行為與情緒",
        short_definition="有利於股價上漲的資訊（如業績改善、資金寬鬆、景氣轉好等）。",
        long_definition=("指刺激股價上漲的資訊，如上市公司經營業績好轉、銀行利率降低、社會資金充足、信貸資金放寬、市場繁榮等，"
                         "以及其他政治、經濟、軍事、外交等方面對股價上漲有利的資訊。"),
        lang="zh-TW",
    )

    # 利空
    upsert_term_orm(
        term="利空",
        aliases=["壞消息", "Negative News", "Bearish News"],
        category="投資行為與情緒",
        short_definition="不利於股價下跌的資訊（如業績惡化、緊縮、衰退、通膨、天災等）。",
        long_definition=("指能夠促使股價下跌的資訊，如上市公司經營業績惡化、銀行緊縮、銀行利率調高、經濟衰退、通貨膨脹、天災人禍等，"
                         "以及其他政治、經濟軍事、外交等方面促使股價下跌的不利消息。"),
        lang="zh-TW",
    )

    # 多頭
    upsert_term_orm(
        term="多頭",
        aliases=["牛市", "多頭市場", "Bull", "Bull Market"],
        category="市場趨勢",
        short_definition="看好後市、預期價格上漲並採取買進策略；長期上漲環境稱多頭市場（牛市）。",
        long_definition=(
            "指投資者對市場前景持樂觀看法，預期股價將持續上漲，因此會選擇在價格相對較低時買進，"
            "待股價上漲後再賣出以獲取價差。當整體股市呈現長期上漲趨勢、買氣旺盛、投資信心高漲時，"
            "便形成所謂的多頭市場，又稱為牛市。常見特徵是「大漲小跌」，走勢穩定上行。"
        ),
        lang="zh-TW",
    )

    # 空頭
    upsert_term_orm(
        term="空頭",
        aliases=["熊市", "空頭市場", "Bear", "Bear Market"],
        category="市場趨勢",
        short_definition="看壞後市、預期價格下跌並可能先賣後買回；長期下跌環境稱空頭市場（熊市）。",
        long_definition=(
            "與多頭相對，指投資者對股市前景悲觀，預期股價將走跌，因此可能透過融券操作先賣出、待下跌後再買回賺取差價。"
            "當市場普遍缺乏信心、賣壓沉重、股價長期下行，就會形成空頭市場，又稱熊市。熊市特徵是「大跌小漲」，"
            "情緒保守甚至恐慌性拋售。"
        ),
        lang="zh-TW",
    )

    # 軋空
    upsert_term_orm(
        term="軋空",
        aliases=["Short Squeeze", "short squeeze"],
        category="市場趨勢",
        short_definition="空單回補買盤擠壓，導致股價被推升的現象。",
        long_definition=(
            "股市上的股票持有者一致認為當天股票將會大下跌，於是多數人卻搶賣空頭帽子賣出股票，"
            "然而當天股價並沒有大幅度下跌，無法低價買進股票。股市結束前，做空頭的只好競相補進，"
            "從而出現收盤價大幅度上升的局面。"
        ),
        lang="zh-TW",
    )

    # 跳空
    upsert_term_orm(
        term="跳空",
        aliases=["跳空缺口", "Gap", "Gap Up", "Gap Down"],
        category="技術分析",
        short_definition="受利多/利空影響，股價開盤或盤中與前日收盤出現明顯缺口的跳躍現象。",
        long_definition=(
            "股價受利多或利空影響後，出現較大幅度上下跳動的現象。當受利多影響上漲時，"
            "當天開盤價或最低價高於前一天收盤價兩個申報單位以上；下跌時，當天開盤價或最高價"
            "低於前一天收盤價在兩個申報單位以上。或在一天交易中上漲或下跌超過一個申報單位，"
            "以上稱為跳空。"
        ),
        lang="zh-TW",
    )

    # 大盤
    upsert_term_orm(
        term="大盤",
        aliases=["加權指數", "大盤指數", "Market Index"],
        category="指數與大盤",
        short_definition="股市整體表現的指標，在台灣常指加權股價指數。",
        long_definition="股市的整體表現，也叫「加權指數」。",
        lang="zh-TW",
    )

    # 加權指數
    upsert_term_orm(
        term="加權指數",
        aliases=["臺灣加權股價指數", "TAIEX", "Weighted Stock Index"],
        category="指數與大盤",
        short_definition="以發行量/市值加權計算的股價指數，用以反映整體市場價格水準走勢。",
        long_definition=(
            "發行量加權股價指數，為台灣證券交易所編製，係將每種選樣股票的每天收盤價格乘以上市股數計算市價額，"
            "再合計為選樣股票市價總額，除以基期市價總額，再乘以一百予以指數化，以反映整體選樣股票價格水準的走勢。"
        ),
        lang="zh-TW",
    )

    # 狀態價格（state prices）
    upsert_term_orm(
        term="狀態價格",
        aliases=["state prices", "State Prices"],
        category="金融理論",
        short_definition="在特定狀態發生時支付 1、否則支付 0 的資產之當前價格，用於狀態價格定價。",
        long_definition=(
            "指的是在特定的狀態發生時回報為1，否則回報為0的資產在當前的價格。"
            "如果未來有N種狀態且各狀態價格已知，則可結合資產在各狀態下回報與無風險利率來對資產定價，"
            "這就是狀態價格定價技術。"
        ),
        lang="zh-TW",
    )

    # 隨機折現率（SDF）
    upsert_term_orm(
        term="隨機折現率",
        aliases=["stochastics discount factor", "stochastic discount factor", "SDF"],
        category="金融理論",
        short_definition="由狀態價格與各狀態機率決定的折現因子（折現率），用於資產定價。",
        long_definition="根據狀態價格以及未來某個狀態發生的機率決定而成的折現因子（折現率）。",
        lang="zh-TW",
    )

    # Payoff
    upsert_term_orm(
        term="Payoff",
        aliases=["報酬", "收益", "payoff"],
        category="金融理論",
        short_definition="資產或策略在某些狀態下的報酬/收益（到期或特定時間的支付）。",
        long_definition="報酬、收益。",
        lang="zh-TW",
    )

    # 無風險利率
    upsert_term_orm(
        term="無風險利率",
        aliases=["Risk-free Rate", "risk-free rate", "Rf"],
        category="利率與報酬",
        short_definition="理論上無風險投資可獲得的報酬率，常用作折現或定價基準。",
        long_definition="指一項沒有風險的投資可得到的理論投資報酬率。",
        lang="zh-TW",
    )

    # 乖離率
    upsert_term_orm(
        term="乖離率",
        aliases=["BIAS", "Bias Ratio"],
        category="技術分析",
        short_definition="衡量股價偏離平均值（如均線）的程度，常以百分比表示。",
        long_definition=("乖離率＝（當日股價－當日平均值）×100﹪。乖離率為正表示股價高於平均值；"
                         "股價越高乖離率越大，上漲力道可能越強；若股價低於平均值則乖離率為負，跌勢可能較猛。"),
        lang="zh-TW",
    )

    # 當日沖銷
    upsert_term_orm(
        term="當日沖銷",
        aliases=["當沖", "Day Trading", "Intraday Trading"],
        category="交易方式",
        short_definition="同一標的在同一天買進又賣出（或先賣後買）以賺取價差的交易。",
        long_definition="對同一種股票，當天同數額，買進賣出買賣數額當天相互抵銷以賺取差價者。",
        lang="zh-TW",
    )

    # 信用交易
    upsert_term_orm(
        term="信用交易",
        aliases=["Margin Trading", "融資融券"],
        category="信用交易",
        short_definition="透過融資與融券進行的交易型態，需經授信機構取得股票/資金以交割或還券。",
        long_definition=(
            "即融資和融券兩種業務。信用交易必須透過授信機構：證券金融公司於次一營業日或再次一營業日"
            "在臺灣證券交易所集中交易市場以公開方式向該種股票所有人標借、洽借或標購等方式取得該項差額股票，"
            "以依交割或還券之用。"
        ),
        lang="zh-TW",
    )

    # K線圖
    upsert_term_orm(
        term="K線圖",
        aliases=["K線", "陰陽線", "Candlestick", "Candlestick Chart"],
        category="技術分析",
        short_definition="用開盤/收盤/最高/最低價繪製的圖表；方塊與上下影線呈現當日價格範圍。",
        long_definition=(
            "又稱陰陽線，是將每天的開盤價與收盤價畫成直立方塊；若當天最高價大於收盤價或開盤價，"
            "則在方塊上方加畫上影線；若當天最低價小於開盤價或收盤價，則在方塊下方加畫下影線。"
            "陽線方塊多以白色或紅色表示（收紅盤），陰線方塊以黑色表示（收黑盤）。"
        ),
        lang="zh-TW",
    )
    # 坐轎
    upsert_term_orm(
        term="坐轎",
        aliases=["坐轎子"],
        category="投資行為與情緒",
        short_definition="先低價布局，等散戶追價後股價上漲，提前進場者坐享漲幅的操作。",
        long_definition="指先行得知消息，於低價先行買進，等大批散戶跟進追價而時機成熟時，坐享股價開動漲幅。",
        lang="zh-TW",
    )

    # 主力
    upsert_term_orm(
        term="主力",
        aliases=["大戶", "主力資金"],
        category="市場參與者",
        short_definition="可大額進出、對股價造成顯著影響的資金或投資者。",
        long_definition="指那些有辦法在股市中大額進出，對股價造成重大影響的人。",
        lang="zh-TW",
    )

    # 紅盤
    upsert_term_orm(
        term="紅盤",
        aliases=["開紅盤", "紅盤日"],
        category="行情與成交",
        short_definition="元旦或農曆年休市後的第一個交易日上漲稱紅盤（下跌稱黑盤）。",
        long_definition=("指元旦假期之後、與農曆年休假後之第一個交易日的股價上漲，稱之。下跌則稱黑盤。"
                         "國內上漲是紅色、下跌是綠色；國外上漲則常用綠色、下跌用紅色。"),
        lang="zh-TW",
    )

    # 哄抬
    upsert_term_orm(
        term="哄抬",
        aliases=["拉抬", "哄抬股價"],
        category="投資行為與情緒",
        short_definition="先買進再利用消息/炒作抬高股價，以利出貨獲利的操作。",
        long_definition="看好後市，先進行買進，再利用消息及炒做手法來抬高股價，以利出貨者。",
        lang="zh-TW",
    )

    # 套利（arbitrage）
    upsert_term_orm(
        term="套利",
        aliases=["arbitrage", "Arbitrage"],
        category="投資方法",
        short_definition="同一資產在不同市場/情境出現價差時，低買高賣以獲取相對低風險收益。",
        long_definition=(
            "某種實物資產或金融資產（在同一市場或不同市場）擁有兩個價格的情況下，以較低的價格買進，較高的價格賣出，"
            "從而獲取低風險的收益。例如同一股票在不同國家交易所上市，可能因匯率或市場因素造成價格不一致；"
            "交易者可在一處賣出、另一處買入以利用價差立即獲利。"
        ),
        lang="zh-TW",
    )

    # 淨值市價比（book-to-market value）
    upsert_term_orm(
        term="淨值市價比",
        aliases=["帳面價值比市場價值", "book-to-market value", "Book-to-Market Ratio", "B/M", "BM ratio"],
        category="估值指標",
        short_definition="公司帳面價值（淨值）相對於市場價值（市值）的比率；為 PBR 的倒數。",
        long_definition=(
            "淨值市價比（Book-to-Market Ratio）用來比較公司的帳面價值與市場價值，以衡量公司是否被市場低估或高估。"
            "帳面價值通常指會計上的淨值（資產扣除負債後的價值）；市場價值由股價與流通在外股數決定（即市值）。"
            "淨值市價比越高，通常可解讀為市場給予公司的估值相對便宜（股價相對淨值較低）。"
            "許多投資人更熟悉股價淨值比（PBR），而淨值市價比就是 PBR 的倒數。"
        ),
        lang="zh-TW",
    )

    # 年化報酬率
    upsert_term_orm(
        term="年化報酬率",
        aliases=["年化收益率", "Annualized Return", "CAGR"],
        category="利率與報酬",
        short_definition="把一段期間總報酬換算為每年平均報酬，常用幾何平均（CAGR）較能反映實際累積。",
        long_definition=("指投資者投資某資產若干年後，每年平均獲得的報酬率。常見計算方法有算術平均與幾何平均，"
                         "其中幾何平均報酬率能較正確評估實際報酬，因此年化報酬率多泛指幾何年平均報酬率。"),
        lang="zh-TW",
    )

    # 動能投資策略（momentum strategy）
    upsert_term_orm(
        term="動能投資策略",
        aliases=["momentum strategy", "Momentum Strategy", "價格動能", "price momentum", "追高殺低策略"],
        category="投資方法",
        short_definition="基於「漲者續漲、跌者續跌」的價格動能，採追強勢/避弱勢的交易策略。",
        long_definition=(
            "指投資人在投資股票時採取追高殺低法。由於資訊傳遞一層層擴散，股價常呈現漲繼續漲、跌繼續跌的現象，"
            "即所謂價格動能（price momentum）。部分具資訊優勢的投資人可能採用此法並獲得報酬。"
        ),
        lang="zh-TW",
    )

    # 追高殺低
    upsert_term_orm(
        term="追高殺低",
        aliases=["高點進場低點出場"],
        category="投資行為與情緒",
        short_definition="在高點買進、下跌後恐慌賣出（低點出場）的不利投資行為。",
        long_definition=("在股價高點時覺得可以再漲而買進，之後反而下跌；為避免虧更多而賣出，結果賣出後股價又反彈。"
                         "即高點進場，低點出場。"),
        lang="zh-TW",
    )

    # 開盤價
    upsert_term_orm(
        term="開盤價",
        aliases=["Open", "開市價", "開市第一筆"],
        category="行情與成交",
        short_definition="交易日第一筆成交的成交價，即當日開盤價。",
        long_definition=("某種證券在證券交易所每個營業日的第一筆交易，第一筆交易的成交價即為當日開盤價。"
                         "若開市後一段時間無成交，可能以前一日收盤價或交易所指導價格作為開盤價（依市場規定）。"),
        lang="zh-TW",
    )

    # 收盤價
    upsert_term_orm(
        term="收盤價",
        aliases=["Close", "收市價"],
        category="行情與成交",
        short_definition="交易日最後一筆成交的成交價；常作為當日行情基準與次日參考。",
        long_definition=(
            "指某種證券在證券交易所一天交易活動結束前最後一筆交易的成交價格。"
            "如當日沒有成交，則採用最近一次的成交價格作為收盤價。收盤價是當日行情的標準，"
            "也是下一交易日開盤價的重要依據，分析時常以收盤價作為計算基礎。"
        ),
        lang="zh-TW",
    )

    # 買空／做多（long）
    upsert_term_orm(
        term="做多",
        aliases=["買空", "long", "Long", "多單"],
        category="交易方式",
        short_definition="看好未來上漲而先買進，待價格上升後賣出以賺取價差的操作。",
        long_definition=("最常見的股票入門操作方式。投資人看好某檔股票或大盤指數將來會漲，先買進並等待股價上升後再賣出賺取價差。"
                         "做多適用於市場趨勢向上，特別是多頭市場時。"),
        lang="zh-TW",
    )

    # 賣空／放空（short）
    upsert_term_orm(
        term="放空",
        aliases=["賣空", "short", "Short", "空單", "做空"],
        category="交易方式",
        short_definition="預期下跌時先賣出（多透過融券），待下跌後買回回補以賺取差價；風險較高。",
        long_definition=("一種逆勢操作。當投資人預期股價或大盤將下跌，會透過融券先賣出股票，再在股價下跌後買回賺取差價。"
                         "若股價不跌反漲，可能造成較大虧損；空頭市場中較常見。"),
        lang="zh-TW",
    )

    # 價值股
    upsert_term_orm(
        term="價值股",
        aliases=["Value Stock", "Value Stocks"],
        category="市場與產業",
        short_definition="相對其獲利能力/淨值等基本面，被市場估值偏便宜（低估）的股票。",
        long_definition=("價值股就是價值（公司未來獲利能力）相對價格被低估的股票。一般會用股價相對每股盈餘、淨值及現金股利的比例來衡量，"
                         "也就是所謂「便宜的股票」。真正精神是在便宜時買進未來的好公司。"),
        lang="zh-TW",
    )

    # 成長股
    upsert_term_orm(
        term="成長股",
        aliases=["Growth Stock", "Growth Stocks"],
        category="市場與產業",
        short_definition="具有較高成長潛力的公司股票，通常估值較高、波動與風險也較大。",
        long_definition=("指企業具有成長的潛能或動能，因此往往價格較高，也存在較大風險。成長因素可能來自多方面（產業、利潤成長率等）。"
                         "成長股精神在於找到好公司並具較大資本利得潛力。"),
        lang="zh-TW",
    )

    # 股價淨值比（PBR）
    upsert_term_orm(
        term="股價淨值比",
        aliases=["price-to-book ratio", "PBR", "Price-to-Book", "PB"],
        category="估值指標",
        short_definition="PBR = 每股市價 ÷ 每股淨值；PBR 越高通常越不便宜，越低可能越便宜（需搭配其他因素）。",
        long_definition=(
            "股價淨值比(PBR) = 每股市價（price）／每股淨值（book，為該公司的總資產扣除總負債後的價值）。"
            "用來觀察股市價是否符合公司目前價值。可理解為：PBR 高，股價越不便宜、潛在報酬較低；PBR 低，股價較便宜、潛在報酬較高。"
        ),
        lang="zh-TW",
    )

    # 炒股
    upsert_term_orm(
        term="炒股",
        aliases=["炒股票", "短線投機"],
        category="投資行為與情緒",
        short_definition="透過買賣股票價差牟利的投機性操作（通常偏短線）。",
        long_definition="買賣股票，靠做股票生意而牟利。核心內容就是通過買入與賣出之間的股價差額實現套利。",
        lang="zh-TW",
    )

    # 游資（refugee capital）
    upsert_term_orm(
        term="游資",
        aliases=["refugee capital", "熱錢", "hot money", "Hot Money"],
        category="總體與景氣",
        short_definition="國際間快速流動、追逐短期獲利（如匯率/利差）的投機性資金。",
        long_definition="又稱熱錢，一種在國際上迅速流動追求匯率變動利益的短期投機性資金。",
        lang="zh-TW",
    )

    # 實質利率
    upsert_term_orm(
        term="實質利率",
        aliases=["Real Interest Rate"],
        category="利率與報酬",
        short_definition="扣除通膨後的利率，用於反映購買力變化下的真實利率水準。",
        long_definition="將價格因素從名目利率（即一般的牌告利率、借款利率）中扣除，以真實反應出利率水準。",
        lang="zh-TW",
    )
    # 2. 指數
    upsert_term_orm(
        term="指數",
        aliases=["Index", "市場指數", "股價指數"],
        category="ETF與指數",
        short_definition="反映市場或產業整體表現的數字指標（如台灣加權指數、S&P 500）。",
        long_definition=("反映市場或產業整體表現的數字指標，例如「台灣加權指數」代表台股整體走勢，"
                         "「S&P500指數」代表美國500大企業表現。"),
        lang="zh-TW",
    )

    # 3. 成分股
    upsert_term_orm(
        term="成分股",
        aliases=["指數成分股", "Constituent Stocks", "Constituents"],
        category="ETF與指數",
        short_definition="構成某個指數的個別股票；ETF常按指數權重持有這些股票。",
        long_definition=("組成指數的個別股票。例如台灣50指數由市值前50大的上市公司組成，ETF會按比例持有這些股票。"),
        lang="zh-TW",
    )

    # 6. 折價
    upsert_term_orm(
        term="折價",
        aliases=["Discount", "折價交易"],
        category="ETF與指數",
        short_definition="ETF市價低於淨值（NAV）的情況；差距可用折價率表示。",
        long_definition=("當ETF市價低於淨值時稱為折價。例如淨值100元，市價98元，折價2%。此時買進等於用較低價格買到實際價值。"),
        lang="zh-TW",
    )

    # 7. 溢價
    upsert_term_orm(
        term="溢價",
        aliases=["Premium", "溢價交易"],
        category="ETF與指數",
        short_definition="ETF市價高於淨值（NAV）的情況；差距可用溢價率表示。",
        long_definition=("當ETF市價高於淨值時稱為溢價。例如淨值100元，市價102元，溢價2%。此時買進需支付額外成本，可能增加投資風險。"),
        lang="zh-TW",
    )

    # 8. 破發
    upsert_term_orm(
        term="破發",
        aliases=["跌破發行價", "Below Issue Price"],
        category="ETF與指數",
        short_definition="市價跌破發行價格（發行價）的情況。",
        long_definition=("ETF市價跌破發行價格。例如某ETF發行價為15元，若市價跌至14元，即稱為「破發」。"
                         "通常發生在市場行情不佳或投資人信心不足時。"),
        lang="zh-TW",
    )

    # 9. 資產規模
    upsert_term_orm(
        term="資產規模",
        aliases=["AUM", "Assets Under Management", "基金規模"],
        category="ETF與指數",
        short_definition="ETF所管理的總資產金額（AUM）；規模大通常代表持有人多、流動性較佳。",
        long_definition=("ETF管理的總資產金額。規模越大代表越多人持有，流動性通常較好。"
                         "例如元大台灣50(0050)資產規模超過新台幣3,000億元。"),
        lang="zh-TW",
    )

    # 10. 流動性（ETF）
    upsert_term_orm(
        term="流動性",
        aliases=["Liquidity"],
        category="ETF與指數",
        short_definition="ETF在市場上容易買賣成交的程度；流動性高通常買賣價差小、成交快。",
        long_definition=("ETF在市場上容易買賣的程度。流動性高的ETF（如0050）買賣價差小，成交速度快；"
                         "流動性差的ETF可能難以及時成交。"),
        lang="zh-TW",
    )

    # 11. 成交量（ETF）
    upsert_term_orm(
        term="成交量",
        aliases=["Volume", "交易量"],
        category="ETF與指數",
        short_definition="單日內ETF成交的股數（或張數）；成交量大通常代表交易活躍。",
        long_definition="單日內ETF的成交股數。成交量大的ETF代表市場交易活躍，價格波動較小。",
        lang="zh-TW",
    )

    # 12. 內扣費用
    upsert_term_orm(
        term="內扣費用",
        aliases=["總費用率", "Expense Ratio", "TER"],
        category="基金費用",
        short_definition="ETF每年自動從資產中扣除的管理費、保管費等總成本（以%表示）。",
        long_definition=("ETF每年自動從資產中扣除的管理費、保管費等總成本。"
                         "例如0050內扣費用0.43%，代表每年每萬元會扣43元費用。"),
        lang="zh-TW",
    )

    # 13. 保管銀行
    upsert_term_orm(
        term="保管銀行",
        aliases=["託管銀行", "Custodian Bank"],
        category="基金制度",
        short_definition="負責保管ETF資產的金融機構，確保基金資產安全並執行保管相關作業。",
        long_definition=(
            "負責保管ETF資產的金融機構，確保基金資產安全。"
            "例如元大台灣50的保管銀行是中國信託商業銀行；若將收益分配帳戶改為與保管銀行同一間，"
            "收取配息時可能不會扣匯費（依各銀行規定）。"
        ),
        lang="zh-TW",
    )

    # 14. 追蹤誤差
    upsert_term_orm(
        term="追蹤誤差",
        aliases=["Tracking Error"],
        category="ETF與指數",
        short_definition="ETF實際報酬與追蹤指數報酬的差異；越小代表追蹤越精準。",
        long_definition=("ETF實際報酬與追蹤指數的差異。例如某ETF年度報酬10%，其追蹤指數報酬10.5%，追蹤誤差為-0.5%。"
                         "誤差越小代表ETF運作越精準。"),
        lang="zh-TW",
    )

    # 15. 殖利率（ETF）
    upsert_term_orm(
        term="殖利率",
        aliases=["Dividend Yield", "股息殖利率"],
        category="ETF報酬與配息",
        short_definition="年度配息總額 ÷ 當前市價（%）；高殖利率可能伴隨高風險。",
        long_definition=("年度配息總額除以當前市價的比率。例如某ETF市價100元，年度配息5元，殖利率即為5%。"
                         "但需注意高殖利率可能伴隨高風險。"),
        lang="zh-TW",
    )

    # 16. 配息
    upsert_term_orm(
        term="配息",
        aliases=["股息分配", "Dividend Distribution"],
        category="ETF報酬與配息",
        short_definition="ETF把投資標的收益（股息/利息等）以現金方式分配給投資人的收益。",
        long_definition=("ETF將投資標的（如股票股息、債券利息）分配給投資人的現金收益。"
                         "常見配息頻率包含月配、季配、年配，例如國泰永續高股息(00878)為季配息。"),
        lang="zh-TW",
    )

    # 17. 除息（ETF）
    upsert_term_orm(
        term="除息",
        aliases=["除息日", "Ex-dividend", "Ex-Dividend"],
        category="ETF報酬與配息",
        short_definition="配息權利切割日；除息日參考價通常會扣除配息金額。",
        long_definition=("ETF配息時，股價會扣除配發金額的過程。例如除息前收盤價100元，配息3元，除息日參考價即為97元。"),
        lang="zh-TW",
    )

    # 18. 填息（ETF）
    upsert_term_orm(
        term="填息",
        aliases=["填息完成"],
        category="ETF報酬與配息",
        short_definition="除息後股價回升至除息前水準，回補除息缺口的現象。",
        long_definition=("除息後股價回升至除息前價位。例如除息後股價從97元漲回100元，即完成填息。填息速度影響實際報酬。"),
        lang="zh-TW",
    )

    # 19. 貼息
    upsert_term_orm(
        term="貼息",
        aliases=["未填息", "貼息狀態"],
        category="ETF報酬與配息",
        short_definition="除息後股價仍低於除息前水準，除息缺口未回補的情況。",
        long_definition=("除息後股價持續低於除息前價位。例如除息後股價從97元跌至95元，投資人雖領到3元股息，但股價損失5元，"
                         "整體仍虧損2元。"),
        lang="zh-TW",
    )

    # 20. 最後買進日
    upsert_term_orm(
        term="最後買進日",
        aliases=["最後買進日(配息)", "最後買進日(參與配息)", "Last Buy Date"],
        category="ETF報酬與配息",
        short_definition="想參與本次配息，最晚需在此交易日（含）前買進並持有；通常為除息日前一個交易日。",
        long_definition="想參與本次配息，最晚需在此交易日（含）前買進並持有ETF。最後買進日為除息日的前一個交易日。",
        lang="zh-TW",
    )

    # 21. 收益平準金
    upsert_term_orm(
        term="收益平準金",
        aliases=["平準金", "收益平準機制"],
        category="ETF報酬與配息",
        short_definition="為避免大量申購稀釋配息，將部分收益先存入平準金以維持配息穩定的機制。",
        long_definition=("防止大量新資金流入導致配息被稀釋的特殊機制。當大量資金申購ETF時，部分收益會存入平準金，"
                         "維持配息穩定性。常見於高股息ETF。"),
        lang="zh-TW",
    )

    # 22. 年化報酬率
    upsert_term_orm(
        term="年化報酬率",
        aliases=["Annualized Return", "CAGR", "年化收益率"],
        category="ETF報酬與配息",
        short_definition="將投資期間總報酬換算為年度平均報酬率，常用於比較不同標的績效。",
        long_definition=("將投資期間的總報酬換算為年度平均報酬率。例如3年總報酬30%，年化報酬率約9.1%。"
                         "用於比較不同投資標的的績效。"),
        lang="zh-TW",
    )

    # 23. 複利
    upsert_term_orm(
        term="複利",
        aliases=["Compound Interest", "複利效果"],
        category="ETF報酬與配息",
        short_definition="將收益再投入，使收益也能再產生收益的滾動成長效果。",
        long_definition=("將收益再投入產生額外收益的滾動效果。例如本金100萬元，年報酬率5%，20年後可成長至265萬元，"
                         "比單利多出65萬元。"),
        lang="zh-TW",
    )

    # 24. 資本利得
    upsert_term_orm(
        term="資本利得",
        aliases=["Capital Gain", "價差收益"],
        category="ETF報酬與配息",
        short_definition="透過買賣ETF（或資產）賺取的價差收益。",
        long_definition=("透過買賣ETF賺取的價差收益。例如以100元買進，120元賣出，資本利得為20元。"
                         "（實務上稅負依地區法規而異。）"),
        lang="zh-TW",
    )

    # 25. 股息再投入
    upsert_term_orm(
        term="股息再投入",
        aliases=["股利再投入", "Dividend Reinvestment", "DRIP"],
        category="ETF報酬與配息",
        short_definition="將從股票中獲得的股息再投資於同一支股票或其他股票，以獲得複利增長。",
        long_definition=("將從股票中獲得的股息再投資於同一支股票或其他股票，以獲得複利增長。"),
        lang="zh-TW",
    )
    # 26. 股票型ETF
    upsert_term_orm(
        term="股票型ETF",
        aliases=["Equity ETF", "股票ETF"],
        category="ETF分類",
        short_definition="主要投資股票的ETF，常見細分包含市值型、高股息型、國際型、主題型等。",
        long_definition=(
            "股票型ETF：主要投資股票市場，可依策略/篩選邏輯再細分：\n"
            "• 市值型：追蹤大型股指數，例如元大台灣50(0050)、元大S&P500(00646)。\n"
            "• 高股息型：篩選高配息公司，例如元大高股息(0056)、國泰永續高股息(00878)。\n"
            "• 國際型：投資海外市場，例如元大S&P500(00646)、統一FANG+(00757)。\n"
            "• 主題型：聚焦特定產業或趨勢，例如國泰台灣科技龍頭(00881)、群益半導體收益(00927)。"
        ),
        lang="zh-TW",
    )

    # 27. 債券型ETF
    upsert_term_orm(
        term="債券型ETF",
        aliases=["Bond ETF", "債券ETF"],
        category="ETF分類",
        short_definition="主要投資債券的ETF，可依政府債/公司債/高收益債與天期長短做分類。",
        long_definition=(
            "債券型ETF：主要投資債券市場，常見分類：\n"
            "• 政府債：投資國家公債，例如元大美債20年(00679B)。\n"
            "• 公司債：投資企業發行債券，例如元大投資級公司債(00720B)。\n"
            "• 高收益債：投資信用評級較低但利息較高的債券，例如凱基美國非投等債(00945B)。\n"
            "• 天期差異：短期（1-3年）、中期（3-10年）、長期（10年以上）債券；天期越長，利率風險通常越高。"
        ),
        lang="zh-TW",
    )

    # 28. 商品型ETF
    upsert_term_orm(
        term="商品型ETF",
        aliases=["Commodity ETF", "商品ETF", "原物料ETF"],
        category="ETF分類",
        short_definition="追蹤黃金、原油、農產品等商品價格的ETF；需注意期貨轉倉成本可能侵蝕報酬。",
        long_definition=("追蹤黃金、原油、農產品等原物料價格。例如元大S&P黃金(00635U）、街口布蘭特油正2(00715L）。"
                         "需注意期貨轉倉成本可能侵蝕報酬。"),
        lang="zh-TW",
    )

    # 29. 主動型ETF
    upsert_term_orm(
        term="主動型ETF",
        aliases=["Active ETF", "主動式ETF"],
        category="ETF分類",
        short_definition="由基金經理人主動選股與調整持股，不完全依指數配置的ETF。",
        long_definition=("由基金經理人主動選股，不完全追蹤指數。例如野村臺灣智慧優選主動式ETF(00980A)，"
                         "持股比例由經理人調整。"),
        lang="zh-TW",
    )

    # 30. 槓桿型ETF
    upsert_term_orm(
        term="槓桿型ETF",
        aliases=["Leveraged ETF", "槓桿ETF", "正2", "正3"],
        category="ETF分類",
        short_definition="用衍生性商品放大指數單日漲跌幅（如2倍）；較適合短線，長持可能因波動耗損。",
        long_definition=("透過衍生性金融商品放大指數漲跌幅。例如元大台灣50正2(00631L)追求單日2倍報酬，"
                         "適合短線交易，長期持有可能因波動耗損而虧損。"),
        lang="zh-TW",
    )

    # 31. 反向型ETF
    upsert_term_orm(
        term="反向型ETF",
        aliases=["Inverse ETF", "反向ETF", "反1"],
        category="ETF分類",
        short_definition="追求指數單日反向報酬（跌則漲）；因每日調整特性，長期報酬可能偏離預期。",
        long_definition=("追求指數反向報酬，例如元大台灣50反1(00632R)在指數下跌時上漲。"
                         "需注意每日調整特性，長期報酬可能偏離預期。"),
        lang="zh-TW",
    )
    # 45. 核心、衛星持股
    upsert_term_orm(
        term="核心、衛星持股",
        aliases=["核心衛星策略", "Core-Satellite Strategy"],
        category="投資方法",
        short_definition="以穩健核心ETF為主、搭配高成長衛星ETF的資產配置策略。",
        long_definition=(
            "核心、衛星持股是一種投資組合配置方式。\n"
            "• 核心持股：投資組合中占比最高、風險較低的穩健型ETF，例如0050、元大美債20年，"
            "通常佔60–80%資金，用來提供穩定報酬。\n"
            "• 衛星持股：占比較低、追求超額報酬的主題型或產業型ETF，例如半導體ETF、AI主題ETF，"
            "通常佔20–40%資金，用來提升整體報酬潛力。"
        ),
        lang="zh-TW",
    )

    # 46. 再平衡
    upsert_term_orm(
        term="再平衡",
        aliases=["Rebalancing", "資產再平衡"],
        category="投資方法",
        short_definition="定期將投資組合比例調整回原先設定，以控制風險並維持策略一致。",
        long_definition=("指定期調整各類ETF或資產的比例至原始設定。例如年初設定股債比為6:4，"
                         "若股票上漲使比例變為7:3，則賣出部分股票ETF並買進債券ETF，使比例回到6:4。"),
        lang="zh-TW",
    )

    # 47. 回測
    upsert_term_orm(
        term="回測",
        aliases=["Backtesting", "歷史回測"],
        category="投資分析",
        short_definition="使用歷史資料模擬投資策略表現，以評估其有效性。",
        long_definition=("指利用歷史數據來驗證投資策略的有效性。例如回測「每月定期定額0050十年」的報酬率。"
                         "但需注意，過去績效不保證未來結果。"),
        lang="zh-TW",
    )

    # 48. 波動率
    upsert_term_orm(
        term="波動率",
        aliases=["Volatility", "年化波動率"],
        category="風險指標",
        short_definition="衡量ETF或資產價格波動幅度的指標；波動越大通常代表風險越高。",
        long_definition=("衡量ETF價格波動程度的指標。例如美股SPY ETF近十年年化波動率約15%，"
                         "新興市場ETF可能高達25%。一般而言，波動越大代表價格起伏越劇烈、風險越高。"),
        lang="zh-TW",
    )

    # 49. 最大回撤
    upsert_term_orm(
        term="最大回撤",
        aliases=["Maximum Drawdown", "Max Drawdown"],
        category="風險指標",
        short_definition="投資期間從高點到低點所出現的最大跌幅，用來衡量極端風險。",
        long_definition=("指投資期間內，資產價格或投資組合從歷史高點下跌到隨後低點的最大跌幅。"
                         "例如2008年金融海嘯期間，SPY ETF的最大回撤約為-50%。"),
        lang="zh-TW",
    )

    # 50. 夏普值
    upsert_term_orm(
        term="夏普值",
        aliases=["Sharpe Ratio", "Sharpe"],
        category="績效指標",
        short_definition="衡量每承擔一單位風險可獲得多少超額報酬的指標；數值越高越好。",
        long_definition=(
            "用來評估投資組合在考慮風險後的報酬效率。其概念為「每承擔一單位風險，"
            "可獲得多少超額報酬」。例如夏普值1.0，代表每1%波動可獲得1%的超額報酬；"
            "在其他條件相同下，夏普值越高，表示績效風險比越佳。"
        ),
        lang="zh-TW",
    )


print("✅ 已成功匯入：股票交易與行情相關專有名詞")

if __name__ == "__main__":
    main()
