# Copyright: Ankitects Pty Ltd and contributors
# License: GNU AGPL, version 3 or later; http://www.gnu.org/licenses/agpl.html

"""Curated CFA Level I sample recall cards (Anki Speedrun).

Hand-authored fixture data used to build the demo/e2e deck. Two-level tag
taxonomy per PHASE1_PLAN M0:

* ``cfa::topic::<area>`` - exactly one of the 10 official topic areas per
  note; drives the mastery RPC, coverage and readiness weights.
* ``cluster::<topic>::<family>`` - curated confusable families; drives
  contrast scheduling. Only genuinely confusable look-alikes are clustered
  (interleaving is neutral-to-negative for merely-similar material).

No AI was used at runtime and none of this is generated at runtime: the app
ships and reviews this data exactly like any imported deck.
"""

# (front, back, topic, cluster-or-None)
CARDS: list[tuple[str, str, str, str | None]] = [
    # ------------------------------------------------------------------
    # Ethics - the Standards I-VII are classic confusables
    # ------------------------------------------------------------------
    (
        "Standard I of the CFA Code of Standards covers what?",
        "Professionalism (knowledge of the law, independence and objectivity, misrepresentation, misconduct, competence).",
        "ethics",
        "cluster::ethics::standards",
    ),
    (
        "Standard II of the CFA Code of Standards covers what?",
        "Integrity of Capital Markets (material non-public information, market manipulation).",
        "ethics",
        "cluster::ethics::standards",
    ),
    (
        "Standard III of the CFA Code of Standards covers what?",
        "Duties to Clients (loyalty/prudence/care, fair dealing, suitability, performance presentation, confidentiality).",
        "ethics",
        "cluster::ethics::standards",
    ),
    (
        "Standard IV of the CFA Code of Standards covers what?",
        "Duties to Employers (loyalty, additional compensation arrangements, responsibilities of supervisors).",
        "ethics",
        "cluster::ethics::standards",
    ),
    (
        "Standard V of the CFA Code of Standards covers what?",
        "Investment Analysis, Recommendations, and Actions (diligence and reasonable basis, communication, record retention).",
        "ethics",
        "cluster::ethics::standards",
    ),
    (
        "Standard VI of the CFA Code of Standards covers what?",
        "Conflicts of Interest (disclosure of conflicts, priority of transactions, referral fees).",
        "ethics",
        "cluster::ethics::standards",
    ),
    (
        "Standard VII of the CFA Code of Standards covers what?",
        "Responsibilities as a CFA Institute Member or Candidate (conduct in the program, referencing the designation).",
        "ethics",
        "cluster::ethics::standards",
    ),
    (
        "Under the GIPS standards, what is a composite?",
        "An aggregation of one or more portfolios managed according to a similar investment mandate, objective, or strategy.",
        "ethics",
        None,
    ),
    (
        "What are the six components of the CFA Institute Code of Ethics about, in one line?",
        "Act with integrity/competence/diligence, put clients above self, use reasonable care, practice ethically, promote market integrity, maintain professional competence.",
        "ethics",
        None,
    ),
    # ------------------------------------------------------------------
    # Quantitative Methods
    # ------------------------------------------------------------------
    (
        "Holding period return formula?",
        "HPR = (ending value - beginning value + income) / beginning value.",
        "quantitative_methods",
        None,
    ),
    (
        "Time-weighted vs money-weighted return: which ignores the timing of external cash flows?",
        "Time-weighted return - it chains sub-period returns and removes the effect of cash-flow timing.",
        "quantitative_methods",
        "cluster::quant::return_measures",
    ),
    (
        "Money-weighted return is equivalent to what familiar quantity?",
        "The internal rate of return (IRR) of the portfolio's cash flows.",
        "quantitative_methods",
        "cluster::quant::return_measures",
    ),
    (
        "Geometric mean return vs arithmetic mean return: which is always smaller or equal, and why?",
        "Geometric <= arithmetic; the gap grows with return volatility (variance drag).",
        "quantitative_methods",
        "cluster::quant::return_measures",
    ),
    (
        "Type I vs Type II error: define Type I.",
        "Rejecting a true null hypothesis (false positive); its probability is the significance level alpha.",
        "quantitative_methods",
        "cluster::quant::hypothesis_errors",
    ),
    (
        "Type I vs Type II error: define Type II.",
        "Failing to reject a false null hypothesis (false negative); its probability is beta, and power = 1 - beta.",
        "quantitative_methods",
        "cluster::quant::hypothesis_errors",
    ),
    (
        "Bayes' formula, stated for updating P(Event | Information)?",
        "P(E|I) = P(I|E) x P(E) / P(I).",
        "quantitative_methods",
        None,
    ),
    (
        "Coefficient of variation formula and use?",
        "CV = standard deviation / mean; risk per unit of return, lower is better for comparing dispersion across datasets.",
        "quantitative_methods",
        None,
    ),
    # ------------------------------------------------------------------
    # Economics
    # ------------------------------------------------------------------
    (
        "Price elasticity of demand formula?",
        "Percentage change in quantity demanded divided by percentage change in price.",
        "economics",
        None,
    ),
    (
        "Normal vs inferior good: what sign does income elasticity take for each?",
        "Normal good: positive income elasticity. Inferior good: negative income elasticity.",
        "economics",
        "cluster::econ::goods_types",
    ),
    (
        "Substitutes vs complements: what sign does cross-price elasticity take for each?",
        "Substitutes: positive cross-price elasticity. Complements: negative.",
        "economics",
        "cluster::econ::goods_types",
    ),
    (
        "Which market structure has many firms, differentiated products, and low entry barriers?",
        "Monopolistic competition.",
        "economics",
        "cluster::econ::market_structures",
    ),
    (
        "Which market structure has few interdependent firms and high entry barriers?",
        "Oligopoly.",
        "economics",
        "cluster::econ::market_structures",
    ),
    (
        "GDP deflator formula?",
        "Nominal GDP / Real GDP x 100.",
        "economics",
        None,
    ),
    (
        "Under a currency peg, which policy tool does the central bank effectively give up?",
        "Independent monetary policy (interest rates must defend the peg).",
        "economics",
        None,
    ),
    # ------------------------------------------------------------------
    # Financial Statement Analysis - FIFO/LIFO/weighted-average
    # ------------------------------------------------------------------
    (
        "In a period of rising prices, which inventory method reports the highest ending inventory: FIFO, LIFO, or weighted average?",
        "FIFO - ending inventory holds the newest, most expensive units.",
        "financial_statement_analysis",
        "cluster::fsa::inventory_cost_flow",
    ),
    (
        "In a period of rising prices, which inventory method reports the highest COGS: FIFO, LIFO, or weighted average?",
        "LIFO - cost of goods sold uses the newest, most expensive units (US GAAP only).",
        "financial_statement_analysis",
        "cluster::fsa::inventory_cost_flow",
    ),
    (
        "Weighted-average inventory costing: how is the unit cost computed?",
        "Total cost of goods available for sale divided by total units available; falls between FIFO and LIFO results.",
        "financial_statement_analysis",
        "cluster::fsa::inventory_cost_flow",
    ),
    (
        "Which inventory method is prohibited under IFRS?",
        "LIFO.",
        "financial_statement_analysis",
        "cluster::fsa::inventory_cost_flow",
    ),
    (
        "Basic EPS formula?",
        "(Net income - preferred dividends) / weighted average common shares outstanding.",
        "financial_statement_analysis",
        None,
    ),
    (
        "Where do unrealized gains on available-for-sale (FVOCI) securities go?",
        "Other comprehensive income (equity), not profit or loss.",
        "financial_statement_analysis",
        None,
    ),
    (
        "Operating vs finance lease for the lessee under IFRS 16: how many lease models are there?",
        "One - essentially all leases are capitalized (right-of-use asset + lease liability).",
        "financial_statement_analysis",
        None,
    ),
    (
        "Current ratio vs quick ratio: what does the quick ratio exclude?",
        "Inventory (and other less-liquid current assets): (cash + short-term marketable securities + receivables) / current liabilities.",
        "financial_statement_analysis",
        "cluster::fsa::liquidity_ratios",
    ),
    (
        "Cash ratio: numerator?",
        "Cash plus short-term marketable securities only, divided by current liabilities.",
        "financial_statement_analysis",
        "cluster::fsa::liquidity_ratios",
    ),
    # ------------------------------------------------------------------
    # Corporate Issuers
    # ------------------------------------------------------------------
    (
        "Weighted average cost of capital (WACC) formula?",
        "WACC = wd x rd x (1 - t) + wp x rp + we x re.",
        "corporate_issuers",
        None,
    ),
    (
        "NPV vs IRR: which decision rule is preferred when they conflict for mutually exclusive projects, and why?",
        "NPV - it assumes reinvestment at the cost of capital and directly measures added value.",
        "corporate_issuers",
        "cluster::corp::capital_budgeting",
    ),
    (
        "IRR definition?",
        "The discount rate that makes a project's NPV equal to zero.",
        "corporate_issuers",
        "cluster::corp::capital_budgeting",
    ),
    (
        "Degree of operating leverage (DOL) formula?",
        "Percentage change in operating income / percentage change in unit sales.",
        "corporate_issuers",
        None,
    ),
    (
        "A share repurchase with borrowed funds raises EPS when...?",
        "The after-tax cost of debt is less than the earnings yield (E/P) of the shares bought back.",
        "corporate_issuers",
        None,
    ),
    # ------------------------------------------------------------------
    # Equity Investments
    # ------------------------------------------------------------------
    (
        "Gordon growth model price formula?",
        "P0 = D1 / (r - g), requiring r > g and stable growth.",
        "equity_investments",
        None,
    ),
    (
        "Weak-form market efficiency: prices reflect what information?",
        "All past market data (prices and volume); technical analysis should not earn abnormal returns.",
        "equity_investments",
        "cluster::equity::market_efficiency",
    ),
    (
        "Semi-strong form market efficiency: prices reflect what information?",
        "All public information; fundamental analysis of public data should not earn abnormal returns.",
        "equity_investments",
        "cluster::equity::market_efficiency",
    ),
    (
        "Strong-form market efficiency: prices reflect what information?",
        "All information, public and private; even insider information earns no abnormal return.",
        "equity_investments",
        "cluster::equity::market_efficiency",
    ),
    (
        "Justified trailing P/E from the dividend discount model?",
        "P0/E0 = payout ratio x (1 + g) / (r - g).",
        "equity_investments",
        None,
    ),
    (
        "Cyclical vs defensive company: which has earnings highly sensitive to the business cycle?",
        "Cyclical (e.g. autos, housing); defensive/non-cyclical demand is stable (e.g. utilities, staples).",
        "equity_investments",
        None,
    ),
    # ------------------------------------------------------------------
    # Fixed Income - the duration trio is the flagship confusable family
    # ------------------------------------------------------------------
    (
        "Macaulay duration: definition?",
        "The weighted-average time to receipt of a bond's cash flows, weights = PV of each cash flow / bond price.",
        "fixed_income",
        "cluster::fi::duration",
    ),
    (
        "Modified duration: definition and relation to Macaulay duration?",
        "Approximate percentage price change for a 1% change in yield; ModDur = MacDur / (1 + periodic yield).",
        "fixed_income",
        "cluster::fi::duration",
    ),
    (
        "Effective duration: definition and when must it be used?",
        "Price sensitivity computed from shifting the benchmark curve; required for bonds with embedded options (callables, MBS) whose cash flows change with rates.",
        "fixed_income",
        "cluster::fi::duration",
    ),
    (
        "Key rate duration measures sensitivity to what?",
        "A change in a single maturity point on the yield curve, holding other rates constant (shaping risk).",
        "fixed_income",
        "cluster::fi::duration",
    ),
    (
        "Bond price and yield move in which relationship?",
        "Inverse and convex - prices rise when yields fall, and gains from a yield drop exceed losses from an equal rise.",
        "fixed_income",
        None,
    ),
    (
        "Current yield formula?",
        "Annual coupon payment / current bond price.",
        "fixed_income",
        None,
    ),
    (
        "G-spread vs Z-spread: define G-spread.",
        "Yield spread over an actual or interpolated government bond yield of matching maturity.",
        "fixed_income",
        "cluster::fi::spreads",
    ),
    (
        "G-spread vs Z-spread: define Z-spread.",
        "The constant spread added to every point of the spot curve that reprices the bond exactly.",
        "fixed_income",
        "cluster::fi::spreads",
    ),
    (
        "Option-adjusted spread (OAS) relates to Z-spread how, for a callable bond?",
        "OAS = Z-spread minus the option cost; it removes the embedded option's compensation.",
        "fixed_income",
        "cluster::fi::spreads",
    ),
    # ------------------------------------------------------------------
    # Derivatives - forwards/futures/swaps confusables
    # ------------------------------------------------------------------
    (
        "Forward vs futures contract: name the two biggest structural differences.",
        "Futures are exchange-traded, standardized and marked to market daily with margin; forwards are OTC, customized, and settle at expiration with counterparty risk.",
        "derivatives",
        "cluster::deriv::forward_commitments",
    ),
    (
        "A swap is equivalent to what portfolio of simpler derivatives?",
        "A series of forward contracts settling on successive dates.",
        "derivatives",
        "cluster::deriv::forward_commitments",
    ),
    (
        "Which party bears default risk in a forward vs a futures contract?",
        "Forward: each counterparty bears the other's credit risk. Futures: the clearinghouse guarantees performance.",
        "derivatives",
        "cluster::deriv::forward_commitments",
    ),
    (
        "Call option payoff at expiration?",
        "max(0, S_T - X).",
        "derivatives",
        "cluster::deriv::option_payoffs",
    ),
    (
        "Put option payoff at expiration?",
        "max(0, X - S_T).",
        "derivatives",
        "cluster::deriv::option_payoffs",
    ),
    (
        "Put-call parity for European options?",
        "S0 + p0 = c0 + X / (1 + r)^T  (protective put = fiduciary call).",
        "derivatives",
        None,
    ),
    (
        "No-arbitrage forward price of an asset with no carry costs or benefits?",
        "F0(T) = S0 x (1 + r)^T.",
        "derivatives",
        None,
    ),
    # ------------------------------------------------------------------
    # Alternative Investments
    # ------------------------------------------------------------------
    (
        "Typical hedge fund fee structure nicknamed '2 and 20' means what?",
        "2% management fee on assets plus 20% incentive fee on profits (often above a hurdle, with a high-water mark).",
        "alternative_investments",
        None,
    ),
    (
        "High-water mark provision: what does it prevent?",
        "Paying incentive fees twice on the same gains - fees are due only on new net profits above the previous peak.",
        "alternative_investments",
        None,
    ),
    (
        "Contango vs backwardation: define contango.",
        "Futures price above the spot price (upward-sloping futures curve); negative roll yield for long positions.",
        "alternative_investments",
        "cluster::alt::futures_curves",
    ),
    (
        "Contango vs backwardation: define backwardation.",
        "Futures price below the spot price (downward-sloping curve); positive roll yield for long positions.",
        "alternative_investments",
        "cluster::alt::futures_curves",
    ),
    (
        "Venture capital vs buyout: which typically uses significant debt financing on mature targets?",
        "Buyouts (LBOs); venture capital takes minority equity stakes in early-stage companies.",
        "alternative_investments",
        None,
    ),
    # ------------------------------------------------------------------
    # Portfolio Management
    # ------------------------------------------------------------------
    (
        "Capital market line (CML): what portfolios plot on it?",
        "Combinations of the risk-free asset and the market portfolio; x-axis is total risk (standard deviation).",
        "portfolio_management",
        "cluster::pm::cml_sml",
    ),
    (
        "Security market line (SML): what does it price, and what is on the x-axis?",
        "Any asset or portfolio via CAPM; x-axis is systematic risk (beta), not total risk.",
        "portfolio_management",
        "cluster::pm::cml_sml",
    ),
    (
        "CAPM expected return formula?",
        "E(Ri) = Rf + beta_i x (E(Rm) - Rf).",
        "portfolio_management",
        None,
    ),
    (
        "Sharpe ratio vs Treynor ratio: what risk does each divide by?",
        "Sharpe: total risk (standard deviation). Treynor: systematic risk (beta).",
        "portfolio_management",
        "cluster::pm::risk_adjusted_measures",
    ),
    (
        "Jensen's alpha: definition?",
        "Portfolio return minus the CAPM-required return for its beta; the vertical distance from the SML.",
        "portfolio_management",
        "cluster::pm::risk_adjusted_measures",
    ),
    (
        "M-squared (M2) measure expresses risk-adjusted performance in what units?",
        "Percentage return units - the portfolio is levered/de-levered to market volatility, then compared with the market return.",
        "portfolio_management",
        "cluster::pm::risk_adjusted_measures",
    ),
    (
        "Systematic vs unsystematic risk: which is diversifiable and which is priced?",
        "Unsystematic (firm-specific) risk is diversifiable and unpriced; systematic (market) risk is undiversifiable and rewarded.",
        "portfolio_management",
        None,
    ),
]
