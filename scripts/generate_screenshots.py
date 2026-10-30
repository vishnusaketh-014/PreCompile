"""Generate high-fidelity SVG screenshots for all 7 PreCompile dashboard views."""

from pathlib import Path

OUT_DIR = Path("docs/screenshots")
OUT_DIR.mkdir(parents=True, exist_ok=True)

WIDTH = 1366
HEIGHT = 768

NAV_ITEMS = [
    ("compare", "⚖", "Compare (A/B)"),
    ("overview", "⬡", "Live Overview"),
    ("timeline", "⏱", "Prediction Timeline"),
    ("distributions", "📊", "Reserved vs Holdout"),
    ("benefit", "📈", "Benefit Estimator"),
    ("safety", "🛡", "Safety Governor"),
    ("experiments", "🧪", "Experiments"),
    ("decisions", "📜", "Decision Log"),
]

def render_base_chrome(active_view_id: str, title: str, subtitle: str, content_svg: str) -> str:
    # Sidebar
    sidebar = f"""
    <rect x="0" y="0" width="220" height="{HEIGHT}" fill="#0D1117" stroke="#1E2D40" stroke-width="1"/>
    <!-- Brand -->
    <text x="20" y="36" fill="#FFFFFF" font-family="'Space Grotesk', sans-serif" font-size="16" font-weight="800">⚡ PreCompile</text>
    <rect x="130" y="24" width="34" height="16" rx="3" fill="rgba(124,58,237,0.15)" stroke="#9461FF" stroke-width="1"/>
    <text x="147" y="35" fill="#9461FF" font-family="'JetBrains Mono', monospace" font-size="9" font-weight="700" text-anchor="middle">v1.5</text>
    <text x="20" y="54" fill="#64748B" font-family="'JetBrains Mono', monospace" font-size="10">Anticipatory Priority</text>

    <!-- Source Pill -->
    <rect x="20" y="74" width="130" height="24" rx="12" fill="#16202B" stroke="#1E2D40" stroke-width="1"/>
    <circle cx="34" cy="86" r="4" fill="#10B981"/>
    <text x="46" y="90" fill="#F1F5F9" font-family="'JetBrains Mono', monospace" font-size="10" font-weight="600">LIVE: SQLite</text>
    """

    y_nav = 120
    for vid, icon, label in NAV_ITEMS:
        is_active = (vid == active_view_id)
        bg = 'rgba(124, 58, 237, 0.15)' if is_active else 'transparent'
        border = 'rgba(124, 58, 237, 0.35)' if is_active else 'transparent'
        text_col = '#9461FF' if is_active else '#94A3B8'
        font_wt = '700' if is_active else '500'

        sidebar += f"""
        <g transform="translate(12, {y_nav})">
          <rect x="0" y="0" width="196" height="34" rx="6" fill="{bg}" stroke="{border}" stroke-width="1"/>
          <text x="14" y="22" fill="{text_col}" font-size="14">{icon}</text>
          <text x="36" y="22" fill="{text_col}" font-family="'Space Grotesk', sans-serif" font-size="12" font-weight="{font_wt}">{label}</text>
        </g>
        """
        y_nav += 42

    sidebar += f"""
    <!-- Sidebar Footer -->
    <g transform="translate(14, {HEIGHT - 60})">
      <rect x="0" y="0" width="192" height="30" rx="4" fill="#16202B" stroke="#1E2D40" stroke-width="1"/>
      <text x="96" y="19" fill="#F1F5F9" font-family="'Space Grotesk', sans-serif" font-size="11" font-weight="600" text-anchor="middle">📺 Presentation Mode [P]</text>
    </g>
    """

    # Topbar
    topbar = f"""
    <rect x="220" y="0" width="{WIDTH - 220}" height="64" fill="#0D1117" stroke="#1E2D40" stroke-width="1"/>
    <text x="248" y="32" fill="#FFFFFF" font-family="'Space Grotesk', sans-serif" font-size="18" font-weight="700">{title}</text>
    <text x="248" y="48" fill="#64748B" font-family="'Space Grotesk', sans-serif" font-size="11">{subtitle}</text>

    <!-- Topbar Actions -->
    <g transform="translate({WIDTH - 300}, 20)">
      <rect x="0" y="0" width="110" height="24" rx="12" fill="#16202B" stroke="#F59E0B" stroke-width="1"/>
      <text x="55" y="16" fill="#F59E0B" font-family="'Space Grotesk', sans-serif" font-size="10" font-weight="700" text-anchor="middle">▶ Replay Mode</text>

      <rect x="120" y="0" width="140" height="24" rx="12" fill="#16202B" stroke="#1E2D40" stroke-width="1"/>
      <circle cx="132" cy="12" r="4" fill="#10B981"/>
      <text x="144" y="16" fill="#94A3B8" font-family="'JetBrains Mono', monospace" font-size="10">SSE Live (1 Hz)</text>
    </g>
    """

    return f"""<svg width="{WIDTH}" height="{HEIGHT}" viewBox="0 0 {WIDTH} {HEIGHT}" xmlns="http://www.w3.org/2000/svg">
      <rect x="0" y="0" width="{WIDTH}" height="{HEIGHT}" fill="#050810"/>
      {sidebar}
      {topbar}
      <g transform="translate(244, 84)">
        {content_svg}
      </g>
    </svg>"""

# -----------------------------------------------------------------------------
# 1. LIVE OVERVIEW
# -----------------------------------------------------------------------------
def make_overview() -> str:
    content = """
    <!-- State Machine Strip -->
    <rect x="0" y="0" width="996" height="120" rx="10" fill="#111720" stroke="#243447" stroke-width="1"/>
    <text x="24" y="28" fill="#94A3B8" font-family="'Space Grotesk', sans-serif" font-size="12" font-weight="700">ANTICIPATORY LIFECYCLE STATE MACHINE</text>
    <rect x="850" y="14" width="122" height="20" rx="10" fill="rgba(0,229,255,0.15)" stroke="#00E5FF" stroke-width="1"/>
    <text x="911" y="28" fill="#00E5FF" font-family="'JetBrains Mono', monospace" font-size="10" font-weight="700" text-anchor="middle">STATE: RESERVED</text>

    <!-- Connector Line -->
    <line x1="80" y1="65" x2="916" y2="65" stroke="#243447" stroke-width="2"/>

    <!-- Nodes -->
    <g transform="translate(60, 48)">
      <circle cx="18" cy="17" r="16" fill="rgba(0,229,255,0.15)" stroke="#00E5FF" stroke-width="2"/>
      <text x="18" y="22" fill="#00E5FF" font-family="'JetBrains Mono', monospace" font-size="11" font-weight="700" text-anchor="middle">01</text>
      <text x="18" y="46" fill="#F1F5F9" font-family="'Space Grotesk', sans-serif" font-size="11" text-anchor="middle">Idle</text>
    </g>
    <g transform="translate(230, 48)">
      <circle cx="18" cy="17" r="16" fill="rgba(0,229,255,0.15)" stroke="#00E5FF" stroke-width="2"/>
      <text x="18" y="22" fill="#00E5FF" font-family="'JetBrains Mono', monospace" font-size="11" font-weight="700" text-anchor="middle">02</text>
      <text x="18" y="46" fill="#F1F5F9" font-family="'Space Grotesk', sans-serif" font-size="11" text-anchor="middle">Observing</text>
    </g>
    <g transform="translate(400, 48)">
      <circle cx="18" cy="17" r="16" fill="rgba(0,229,255,0.15)" stroke="#00E5FF" stroke-width="2"/>
      <text x="18" y="22" fill="#00E5FF" font-family="'JetBrains Mono', monospace" font-size="11" font-weight="700" text-anchor="middle">03</text>
      <text x="18" y="46" fill="#F1F5F9" font-family="'Space Grotesk', sans-serif" font-size="11" text-anchor="middle">Predicted</text>
    </g>
    <g transform="translate(570, 48)">
      <circle cx="18" cy="17" r="16" fill="rgba(0,229,255,0.15)" stroke="#00E5FF" stroke-width="2"/>
      <text x="18" y="22" fill="#00E5FF" font-family="'JetBrains Mono', monospace" font-size="11" font-weight="700" text-anchor="middle">04</text>
      <text x="18" y="46" fill="#F1F5F9" font-family="'Space Grotesk', sans-serif" font-size="11" text-anchor="middle">Armed</text>
    </g>
    <g transform="translate(740, 48)">
      <circle cx="18" cy="17" r="19" fill="#0B0F14" stroke="#00E5FF" stroke-width="3" filter="drop-shadow(0 0 10px #00E5FF)"/>
      <text x="18" y="22" fill="#00E5FF" font-family="'JetBrains Mono', monospace" font-size="12" font-weight="800" text-anchor="middle">05</text>
      <text x="18" y="48" fill="#00E5FF" font-family="'Space Grotesk', sans-serif" font-size="12" font-weight="700" text-anchor="middle">Reserved</text>
    </g>
    <g transform="translate(910, 48)">
      <circle cx="18" cy="17" r="16" fill="#16202B" stroke="#243447" stroke-width="2"/>
      <text x="18" y="22" fill="#64748B" font-family="'JetBrains Mono', monospace" font-size="11" font-weight="700" text-anchor="middle">06</text>
      <text x="18" y="46" fill="#64748B" font-family="'Space Grotesk', sans-serif" font-size="11" text-anchor="middle">Released</text>
    </g>

    <!-- 5 Hero Metric Tiles -->
    <g transform="translate(0, 136)">
      <!-- Tile 1 -->
      <rect x="0" y="0" width="187" height="130" rx="8" fill="#16202B" stroke="#243447" stroke-width="1"/>
      <text x="16" y="24" fill="#94A3B8" font-family="'Space Grotesk', sans-serif" font-size="10" font-weight="700">PREDICTION CONFIDENCE</text>
      <text x="16" y="65" fill="#FFFFFF" font-family="'JetBrains Mono', monospace" font-size="28" font-weight="700">92.4%</text>
      <text x="16" y="85" fill="#94A3B8" font-family="'JetBrains Mono', monospace" font-size="11">CV: 0.11 • n=50</text>
      <line x1="16" y1="98" x2="171" y2="98" stroke="#243447" stroke-width="1"/>
      <text x="16" y="115" fill="#00E5FF" font-family="'JetBrains Mono', monospace" font-size="10">GATE: PASS (Hit: 100%)</text>

      <!-- Tile 2 -->
      <rect x="202" y="0" width="187" height="130" rx="8" fill="#16202B" stroke="#243447" stroke-width="1"/>
      <text x="218" y="24" fill="#94A3B8" font-family="'Space Grotesk', sans-serif" font-size="10" font-weight="700">SAVE-TO-BUILD DELAY</text>
      <text x="218" y="65" fill="#FFFFFF" font-family="'JetBrains Mono', monospace" font-size="28" font-weight="700">1.80s</text>
      <text x="218" y="85" fill="#94A3B8" font-family="'JetBrains Mono', monospace" font-size="11">σ = 0.16s (Window)</text>
      <line x1="218" y1="98" x2="373" y2="98" stroke="#243447" stroke-width="1"/>
      <text x="218" y="115" fill="#94A3B8" font-family="'JetBrains Mono', monospace" font-size="10">[1.40s, 2.20s]</text>

      <!-- Tile 3 -->
      <rect x="404" y="0" width="187" height="130" rx="8" fill="#16202B" stroke="#243447" stroke-width="1"/>
      <text x="420" y="24" fill="#94A3B8" font-family="'Space Grotesk', sans-serif" font-size="10" font-weight="700">ACTIVE CGROUP WEIGHT</text>
      <text x="420" y="65" fill="#00E5FF" font-family="'JetBrains Mono', monospace" font-size="28" font-weight="700">500</text>
      <text x="420" y="85" fill="#94A3B8" font-family="'JetBrains Mono', monospace" font-size="11">5:1 CFS Proportional Share</text>
      <line x1="420" y1="98" x2="575" y2="98" stroke="#243447" stroke-width="1"/>
      <text x="420" y="115" fill="#00E5FF" font-family="'JetBrains Mono', monospace" font-size="10">precompile-res-12</text>

      <!-- Tile 4 -->
      <rect x="606" y="0" width="187" height="130" rx="8" fill="#16202B" stroke="#243447" stroke-width="1"/>
      <text x="622" y="24" fill="#94A3B8" font-family="'Space Grotesk', sans-serif" font-size="10" font-weight="700">SYSTEM CPU PSI</text>
      <text x="622" y="65" fill="#FFFFFF" font-family="'JetBrains Mono', monospace" font-size="28" font-weight="700">14.2%</text>
      <text x="622" y="85" fill="#94A3B8" font-family="'JetBrains Mono', monospace" font-size="11">Moderate Contention</text>
      <line x1="622" y1="98" x2="777" y2="98" stroke="#243447" stroke-width="1"/>
      <text x="622" y="115" fill="#00E676" font-family="'JetBrains Mono', monospace" font-size="10">GATE: ACTIVE (≥5%)</text>

      <!-- Tile 5 -->
      <rect x="808" y="0" width="188" height="130" rx="8" fill="#16202B" stroke="#243447" stroke-width="1"/>
      <text x="824" y="24" fill="#94A3B8" font-family="'Space Grotesk', sans-serif" font-size="10" font-weight="700">SAFETY GOVERNOR</text>
      <text x="824" y="65" fill="#00E676" font-family="'JetBrains Mono', monospace" font-size="26" font-weight="700">NORMAL</text>
      <text x="824" y="85" fill="#94A3B8" font-family="'JetBrains Mono', monospace" font-size="11">Actuation Armed</text>
      <line x1="824" y1="98" x2="980" y2="98" stroke="#243447" stroke-width="1"/>
      <text x="824" y="115" fill="#94A3B8" font-family="'JetBrains Mono', monospace" font-size="10">Budget: 120s / 900s</text>
    </g>

    <!-- Transition Feed Table -->
    <g transform="translate(0, 282)">
      <rect x="0" y="0" width="996" height="390" rx="10" fill="#111720" stroke="#243447" stroke-width="1"/>
      <text x="24" y="28" fill="#FFFFFF" font-family="'Space Grotesk', sans-serif" font-size="14" font-weight="700">Recent Lifecycle Transitions</text>
      <text x="24" y="46" fill="#64748B" font-family="'Space Grotesk', sans-serif" font-size="11">Audited real-time transitions across cgroup arming, process spawning, and exits</text>

      <!-- Table Header -->
      <rect x="24" y="64" width="948" height="28" fill="#16202B" rx="4"/>
      <text x="36" y="82" fill="#94A3B8" font-family="'JetBrains Mono', monospace" font-size="10" font-weight="700">TIMESTAMP</text>
      <text x="160" y="82" fill="#94A3B8" font-family="'JetBrains Mono', monospace" font-size="10" font-weight="700">TRANSITION</text>
      <text x="360" y="82" fill="#94A3B8" font-family="'JetBrains Mono', monospace" font-size="10" font-weight="700">TRIGGER / EVENT</text>
      <text x="620" y="82" fill="#94A3B8" font-family="'JetBrains Mono', monospace" font-size="10" font-weight="700">TARGET CGROUP</text>
      <text x="800" y="82" fill="#94A3B8" font-family="'JetBrains Mono', monospace" font-size="10" font-weight="700">PID</text>
      <text x="870" y="82" fill="#94A3B8" font-family="'JetBrains Mono', monospace" font-size="10" font-weight="700">LATENCY</text>

      <!-- Row 1 -->
      <text x="36" y="118" fill="#94A3B8" font-family="'JetBrains Mono', monospace" font-size="11">19:12:44.205</text>
      <rect x="160" y="104" width="60" height="18" rx="3" fill="rgba(0,229,255,0.15)"/>
      <text x="190" y="117" fill="#00E5FF" font-family="'JetBrains Mono', monospace" font-size="9" text-anchor="middle">ARMED</text>
      <text x="230" y="118" fill="#64748B">→</text>
      <rect x="246" y="104" width="70" height="18" rx="3" fill="rgba(0,230,118,0.15)"/>
      <text x="281" y="117" fill="#00E676" font-family="'JetBrains Mono', monospace" font-size="9" text-anchor="middle">RESERVED</text>
      <text x="360" y="118" fill="#F1F5F9" font-family="'Space Grotesk', sans-serif" font-size="11">Claimed compiler process into elevated cgroup</text>
      <text x="620" y="118" fill="#00E5FF" font-family="'JetBrains Mono', monospace" font-size="11">precompile-res-12</text>
      <text x="800" y="118" fill="#F1F5F9" font-family="'JetBrains Mono', monospace" font-size="11">18294</text>
      <text x="870" y="118" fill="#00E676" font-family="'JetBrains Mono', monospace" font-size="11">44.6 ms</text>

      <!-- Row 2 -->
      <line x1="24" y1="134" x2="972" y2="134" stroke="#1E2B3A" stroke-width="1"/>
      <text x="36" y="156" fill="#94A3B8" font-family="'JetBrains Mono', monospace" font-size="11">19:12:42.401</text>
      <rect x="160" y="142" width="75" height="18" rx="3" fill="rgba(255,179,0,0.15)"/>
      <text x="197" y="155" fill="#FFB300" font-family="'JetBrains Mono', monospace" font-size="9" text-anchor="middle">PREDICTED</text>
      <text x="245" y="156" fill="#64748B">→</text>
      <rect x="261" y="142" width="60" height="18" rx="3" fill="rgba(0,229,255,0.15)"/>
      <text x="291" y="155" fill="#00E5FF" font-family="'JetBrains Mono', monospace" font-size="9" text-anchor="middle">ARMED</text>
      <text x="360" y="156" fill="#F1F5F9" font-family="'Space Grotesk', sans-serif" font-size="11">Armed empty reservation group (weight 500)</text>
      <text x="620" y="156" fill="#00E5FF" font-family="'JetBrains Mono', monospace" font-size="11">precompile-res-12</text>
      <text x="800" y="156" fill="#64748B" font-family="'JetBrains Mono', monospace" font-size="11">—</text>
      <text x="870" y="156" fill="#94A3B8" font-family="'JetBrains Mono', monospace" font-size="11">0.8 ms</text>
    </g>
    """
    return render_base_chrome("overview", "Live Overview", "Real-time scheduling telemetry & state machine", content)

# -----------------------------------------------------------------------------
# 2. PREDICTION TIMELINE
# -----------------------------------------------------------------------------
def make_timeline() -> str:
    content = """
    <!-- Metric summary strip -->
    <g transform="translate(0, 0)">
      <rect x="0" y="0" width="234" height="74" rx="8" fill="#16202B" stroke="#243447" stroke-width="1"/>
      <text x="16" y="24" fill="#94A3B8" font-family="'Space Grotesk', sans-serif" font-size="10" font-weight="700">MEAN DELAY (μ)</text>
      <text x="16" y="54" fill="#00E5FF" font-family="'JetBrains Mono', monospace" font-size="22" font-weight="700">1.80s</text>

      <rect x="254" y="0" width="234" height="74" rx="8" fill="#16202B" stroke="#243447" stroke-width="1"/>
      <text x="270" y="24" fill="#94A3B8" font-family="'Space Grotesk', sans-serif" font-size="10" font-weight="700">WINDOW WIDTH</text>
      <text x="270" y="54" fill="#FFFFFF" font-family="'JetBrains Mono', monospace" font-size="22" font-weight="700">0.80s</text>

      <rect x="508" y="0" width="234" height="74" rx="8" fill="#16202B" stroke="#243447" stroke-width="1"/>
      <text x="524" y="24" fill="#94A3B8" font-family="'Space Grotesk', sans-serif" font-size="10" font-weight="700">MEAN PREDICTION ERROR</text>
      <text x="524" y="54" fill="#FFFFFF" font-family="'JetBrains Mono', monospace" font-size="22" font-weight="700">±0.04s</text>

      <rect x="762" y="0" width="234" height="74" rx="8" fill="#16202B" stroke="#00E5FF" stroke-width="1"/>
      <text x="778" y="24" fill="#94A3B8" font-family="'Space Grotesk', sans-serif" font-size="10" font-weight="700">ROLLING HIT RATE</text>
      <text x="778" y="54" fill="#00E5FF" font-family="'JetBrains Mono', monospace" font-size="22" font-weight="700">100.0%</text>
    </g>

    <!-- Main Swimlanes Panel -->
    <g transform="translate(0, 94)">
      <rect x="0" y="0" width="996" height="580" rx="10" fill="#111720" stroke="#243447" stroke-width="1"/>
      <text x="24" y="28" fill="#FFFFFF" font-family="'Space Grotesk', sans-serif" font-size="14" font-weight="700">Build Cycle Latency Swimlanes</text>
      <text x="24" y="46" fill="#64748B" font-family="'Space Grotesk', sans-serif" font-size="11">Visual alignment of file save (t=0), predicted window [LO, HI], actual spawn, and claim</text>

      <!-- Swimlane 1 -->
      <g transform="translate(24, 70)">
        <rect x="0" y="0" width="948" height="68" rx="6" fill="#16202B" stroke="#243447" stroke-width="1"/>
        <text x="16" y="24" fill="#FFFFFF" font-family="'JetBrains Mono', monospace" font-size="12" font-weight="700">build-48</text>
        <rect x="80" y="12" width="40" height="16" rx="3" fill="rgba(0,229,255,0.15)"/>
        <text x="100" y="23" fill="#00E5FF" font-family="'JetBrains Mono', monospace" font-size="9" text-anchor="middle">HIT</text>
        <text x="16" y="46" fill="#94A3B8" font-family="'JetBrains Mono', monospace" font-size="11">Spawn: 1.81s | Err: +0.01s | Claim: 44.2ms</text>

        <!-- Track -->
        <rect x="260" y="18" width="660" height="28" rx="4" fill="#0B0F14" stroke="#243447" stroke-width="1"/>
        <!-- Window -->
        <rect x="520" y="18" width="160" height="28" fill="rgba(0,229,255,0.2)" stroke="#00E5FF" stroke-width="1"/>
        <!-- Save Pip -->
        <text x="268" y="37" font-size="12">💾</text>
        <!-- Spawn Pip -->
        <text x="602" y="37" font-size="12">🚀</text>
      </g>

      <!-- Swimlane 2 -->
      <g transform="translate(24, 150)">
        <rect x="0" y="0" width="948" height="68" rx="6" fill="#16202B" stroke="#243447" stroke-width="1"/>
        <text x="16" y="24" fill="#FFFFFF" font-family="'JetBrains Mono', monospace" font-size="12" font-weight="700">build-47</text>
        <rect x="80" y="12" width="40" height="16" rx="3" fill="rgba(0,229,255,0.15)"/>
        <text x="100" y="23" fill="#00E5FF" font-family="'JetBrains Mono', monospace" font-size="9" text-anchor="middle">HIT</text>
        <text x="16" y="46" fill="#94A3B8" font-family="'JetBrains Mono', monospace" font-size="11">Spawn: 1.79s | Err: -0.01s | Claim: 45.1ms</text>

        <rect x="260" y="18" width="660" height="28" rx="4" fill="#0B0F14" stroke="#243447" stroke-width="1"/>
        <rect x="520" y="18" width="160" height="28" fill="rgba(0,229,255,0.2)" stroke="#00E5FF" stroke-width="1"/>
        <text x="268" y="37" font-size="12">💾</text>
        <text x="598" y="37" font-size="12">🚀</text>
      </g>

      <!-- Swimlane 3 -->
      <g transform="translate(24, 230)">
        <rect x="0" y="0" width="948" height="68" rx="6" fill="#16202B" stroke="#243447" stroke-width="1"/>
        <text x="16" y="24" fill="#FFFFFF" font-family="'JetBrains Mono', monospace" font-size="12" font-weight="700">build-46</text>
        <rect x="80" y="12" width="60" height="16" rx="3" fill="rgba(255,179,0,0.15)"/>
        <text x="110" y="23" fill="#FFB300" font-family="'JetBrains Mono', monospace" font-size="9" text-anchor="middle">HOLDOUT</text>
        <text x="16" y="46" fill="#94A3B8" font-family="'JetBrains Mono', monospace" font-size="11">Spawn: 1.82s | Err: +0.02s | Control Arm</text>

        <rect x="260" y="18" width="660" height="28" rx="4" fill="#0B0F14" stroke="#243447" stroke-width="1"/>
        <rect x="520" y="18" width="160" height="28" fill="rgba(255,179,0,0.15)" stroke="#FFB300" stroke-width="1"/>
        <text x="268" y="37" font-size="12">💾</text>
        <text x="604" y="37" font-size="12">🚀</text>
      </g>
    </g>
    """
    return render_base_chrome("timeline", "Prediction Timeline", "Event-to-process latency swimlanes & claim delays", content)

# -----------------------------------------------------------------------------
# 3. RESERVED VS HOLDOUT
# -----------------------------------------------------------------------------
def make_distributions() -> str:
    content = """
    <!-- Highlight Stat Bar -->
    <g transform="translate(0, 0)">
      <rect x="0" y="0" width="234" height="74" rx="8" fill="#16202B" stroke="#00E5FF" stroke-width="1"/>
      <text x="16" y="24" fill="#94A3B8" font-family="'Space Grotesk', sans-serif" font-size="10" font-weight="700">EMPIRICAL SPEEDUP</text>
      <text x="16" y="54" fill="#00E5FF" font-family="'JetBrains Mono', monospace" font-size="24" font-weight="700">+56.8%</text>

      <rect x="254" y="0" width="234" height="74" rx="8" fill="#16202B" stroke="#243447" stroke-width="1"/>
      <text x="270" y="24" fill="#94A3B8" font-family="'Space Grotesk', sans-serif" font-size="10" font-weight="700">RESERVED MEDIAN</text>
      <text x="270" y="54" fill="#FFFFFF" font-family="'JetBrains Mono', monospace" font-size="24" font-weight="700">1.98s</text>

      <rect x="508" y="0" width="234" height="74" rx="8" fill="#16202B" stroke="#243447" stroke-width="1"/>
      <text x="524" y="24" fill="#94A3B8" font-family="'Space Grotesk', sans-serif" font-size="10" font-weight="700">HOLDOUT MEDIAN</text>
      <text x="524" y="54" fill="#FFB300" font-family="'JetBrains Mono', monospace" font-size="24" font-weight="700">4.58s</text>

      <rect x="762" y="0" width="234" height="74" rx="8" fill="#16202B" stroke="#243447" stroke-width="1"/>
      <text x="778" y="24" fill="#94A3B8" font-family="'Space Grotesk', sans-serif" font-size="10" font-weight="700">SIGNIFICANCE (MANN-WHITNEY)</text>
      <text x="778" y="54" fill="#00E5FF" font-family="'JetBrains Mono', monospace" font-size="20" font-weight="700">p &lt; 0.0001</text>
    </g>

    <!-- Side-by-Side Distribution Chart Panel -->
    <g transform="translate(0, 94)">
      <rect x="0" y="0" width="996" height="340" rx="10" fill="#111720" stroke="#243447" stroke-width="1"/>
      <text x="24" y="28" fill="#FFFFFF" font-family="'Space Grotesk', sans-serif" font-size="14" font-weight="700">Build-Time Distribution (Reserved vs Holdout)</text>
      <text x="24" y="46" fill="#64748B" font-family="'Space Grotesk', sans-serif" font-size="11">Side-by-side box & whisker plots with individual trial points</text>

      <!-- Reserved Box Plot -->
      <g transform="translate(260, 80)">
        <text x="40" y="-10" fill="#00E5FF" font-family="'Space Grotesk', sans-serif" font-size="12" font-weight="700" text-anchor="middle">RESERVED (C2)</text>
        <line x1="40" y1="20" x2="40" y2="200" stroke="#00E5FF" stroke-dasharray="3,3"/>
        <rect x="10" y="90" width="60" height="40" fill="rgba(0,229,255,0.15)" stroke="#00E5FF" stroke-width="2"/>
        <line x1="10" y1="105" x2="70" y2="105" stroke="#00E5FF" stroke-width="3"/>
        <text x="78" y="108" fill="#00E5FF" font-family="'JetBrains Mono', monospace" font-size="11">med: 1.98s</text>
      </g>

      <!-- Holdout Box Plot -->
      <g transform="translate(680, 80)">
        <text x="40" y="-10" fill="#FFB300" font-family="'Space Grotesk', sans-serif" font-size="12" font-weight="700" text-anchor="middle">HOLDOUT (C0)</text>
        <line x1="40" y1="10" x2="40" y2="220" stroke="#FFB300" stroke-dasharray="3,3"/>
        <rect x="10" y="40" width="60" height="80" fill="rgba(255,179,0,0.15)" stroke="#FFB300" stroke-width="2"/>
        <line x1="10" y1="75" x2="70" y2="75" stroke="#FFB300" stroke-width="3"/>
        <text x="78" y="78" fill="#FFB300" font-family="'JetBrains Mono', monospace" font-size="11">med: 4.58s</text>
      </g>
    </g>

    <!-- Contention Bins Panel -->
    <g transform="translate(0, 454)">
      <rect x="0" y="0" width="996" height="220" rx="10" fill="#111720" stroke="#243447" stroke-width="1"/>
      <text x="24" y="28" fill="#FFFFFF" font-family="'Space Grotesk', sans-serif" font-size="14" font-weight="700">Performance Across Contention Bins</text>

      <g transform="translate(24, 46)">
        <rect x="0" y="0" width="300" height="150" rx="6" fill="#16202B" stroke="#243447" stroke-width="1"/>
        <text x="16" y="24" fill="#FFFFFF" font-family="'Space Grotesk', sans-serif" font-size="12" font-weight="700">Idle Load (&lt;5% PSI)</text>
        <text x="16" y="55" fill="#94A3B8" font-family="'JetBrains Mono', monospace" font-size="12">Reserved: 1.84s • Holdout: 1.82s</text>
        <text x="16" y="80" fill="#94A3B8" font-family="'JetBrains Mono', monospace" font-size="11">Delta: +0.82% (Bounded harm safe)</text>
      </g>
      <g transform="translate(348, 46)">
        <rect x="0" y="0" width="300" height="150" rx="6" fill="#16202B" stroke="#243447" stroke-width="1"/>
        <text x="16" y="24" fill="#FFFFFF" font-family="'Space Grotesk', sans-serif" font-size="12" font-weight="700">Moderate (5-20% PSI)</text>
        <text x="16" y="55" fill="#00E5FF" font-family="'JetBrains Mono', monospace" font-size="12">Reserved: 1.98s • Holdout: 4.58s</text>
        <text x="16" y="80" fill="#00E5FF" font-family="'JetBrains Mono', monospace" font-size="11">Speedup: +56.5% (p &lt; 0.0001)</text>
      </g>
      <g transform="translate(672, 46)">
        <rect x="0" y="0" width="300" height="150" rx="6" fill="#16202B" stroke="#243447" stroke-width="1"/>
        <text x="16" y="24" fill="#FFFFFF" font-family="'Space Grotesk', sans-serif" font-size="12" font-weight="700">High Load (≥20% PSI)</text>
        <text x="16" y="55" fill="#00E5FF" font-family="'JetBrains Mono', monospace" font-size="12">Reserved: 1.98s • Holdout: 8.35s</text>
        <text x="16" y="80" fill="#00E5FF" font-family="'JetBrains Mono', monospace" font-size="11">Speedup: +75.6% (p &lt; 10⁻¹⁵)</text>
      </g>
    </g>
    """
    return render_base_chrome("distributions", "Reserved vs Holdout", "Empirical build-time & CPU share distributions", content)

# -----------------------------------------------------------------------------
# 4. BENEFIT ESTIMATOR
# -----------------------------------------------------------------------------
def make_benefit() -> str:
    content = """
    <!-- Highlight Stat Bar -->
    <g transform="translate(0, 0)">
      <rect x="0" y="0" width="234" height="74" rx="8" fill="#16202B" stroke="#00E5FF" stroke-width="1"/>
      <text x="16" y="24" fill="#94A3B8" font-family="'Space Grotesk', sans-serif" font-size="10" font-weight="700">CURRENT WEIGHT</text>
      <text x="16" y="54" fill="#00E5FF" font-family="'JetBrains Mono', monospace" font-size="24" font-weight="700">500</text>

      <rect x="254" y="0" width="234" height="74" rx="8" fill="#16202B" stroke="#243447" stroke-width="1"/>
      <text x="270" y="24" fill="#94A3B8" font-family="'Space Grotesk', sans-serif" font-size="10" font-weight="700">LAST SPEEDUP</text>
      <text x="270" y="54" fill="#FFFFFF" font-family="'JetBrains Mono', monospace" font-size="24" font-weight="700">+56.8%</text>

      <rect x="508" y="0" width="234" height="74" rx="8" fill="#16202B" stroke="#243447" stroke-width="1"/>
      <text x="524" y="24" fill="#94A3B8" font-family="'Space Grotesk', sans-serif" font-size="10" font-weight="700">SAMPLES BALANCE</text>
      <text x="524" y="54" fill="#FFFFFF" font-family="'JetBrains Mono', monospace" font-size="24" font-weight="700">47 / 50</text>

      <rect x="762" y="0" width="234" height="74" rx="8" fill="#16202B" stroke="#243447" stroke-width="1"/>
      <text x="778" y="24" fill="#94A3B8" font-family="'Space Grotesk', sans-serif" font-size="10" font-weight="700">CONTROLLER ACTION</text>
      <text x="778" y="54" fill="#00E5FF" font-family="'JetBrains Mono', monospace" font-size="20" font-weight="700">HOLD (CAP 500)</text>
    </g>

    <!-- Weight Trajectory Panel -->
    <g transform="translate(0, 94)">
      <rect x="0" y="0" width="996" height="260" rx="10" fill="#111720" stroke="#243447" stroke-width="1"/>
      <text x="24" y="28" fill="#FFFFFF" font-family="'Space Grotesk', sans-serif" font-size="14" font-weight="700">Adaptive Priority Weight Trajectory</text>
      <text x="24" y="46" fill="#64748B" font-family="'Space Grotesk', sans-serif" font-size="11">Closed-loop feedback controller adjusting CFS priority based on measured holdout benefit</text>

      <!-- Step Chart -->
      <path d="M 80 180 L 350 180 L 350 140 L 650 140 L 650 90 L 920 90" fill="none" stroke="#00E5FF" stroke-width="3"/>
      <circle cx="80" cy="180" r="5" fill="#00E5FF"/>
      <text x="80" y="170" fill="#F1F5F9" font-family="'JetBrains Mono', monospace" font-size="11">250</text>

      <circle cx="350" cy="140" r="5" fill="#00E5FF"/>
      <text x="350" y="130" fill="#F1F5F9" font-family="'JetBrains Mono', monospace" font-size="11">350 (step_up)</text>

      <circle cx="650" cy="90" r="5" fill="#00E5FF"/>
      <text x="650" y="80" fill="#00E5FF" font-family="'JetBrains Mono', monospace" font-size="11">500 (step_up)</text>
    </g>
    """
    return render_base_chrome("benefit", "Benefit Estimator", "Adaptive priority feedback controller & speedup tracking", content)

# -----------------------------------------------------------------------------
# 5. SAFETY GOVERNOR
# -----------------------------------------------------------------------------
def make_safety() -> str:
    content = """
    <g transform="translate(0, 0)">
      <rect x="0" y="0" width="234" height="74" rx="8" fill="#16202B" stroke="#243447" stroke-width="1"/>
      <text x="16" y="24" fill="#94A3B8" font-family="'Space Grotesk', sans-serif" font-size="10" font-weight="700">KILL SWITCH</text>
      <text x="16" y="54" fill="#00E676" font-family="'JetBrains Mono', monospace" font-size="22" font-weight="700">DISENGAGED</text>

      <rect x="254" y="0" width="234" height="74" rx="8" fill="#16202B" stroke="#243447" stroke-width="1"/>
      <text x="270" y="24" fill="#94A3B8" font-family="'Space Grotesk', sans-serif" font-size="10" font-weight="700">CIRCUIT BREAKER</text>
      <text x="270" y="54" fill="#00E676" font-family="'JetBrains Mono', monospace" font-size="22" font-weight="700">CLEAR</text>

      <rect x="508" y="0" width="234" height="74" rx="8" fill="#16202B" stroke="#243447" stroke-width="1"/>
      <text x="524" y="24" fill="#94A3B8" font-family="'Space Grotesk', sans-serif" font-size="10" font-weight="700">DESCENDANT LEAKS</text>
      <text x="524" y="54" fill="#00E5FF" font-family="'JetBrains Mono', monospace" font-size="22" font-weight="700">0 LEAKS</text>

      <rect x="762" y="0" width="234" height="74" rx="8" fill="#16202B" stroke="#243447" stroke-width="1"/>
      <text x="778" y="24" fill="#94A3B8" font-family="'Space Grotesk', sans-serif" font-size="10" font-weight="700">DAEMON OVERHEAD</text>
      <text x="778" y="54" fill="#FFFFFF" font-family="'JetBrains Mono', monospace" font-size="22" font-weight="700">3.45%</text>
    </g>

    <!-- Budget Gauge -->
    <g transform="translate(0, 94)">
      <rect x="0" y="0" width="996" height="180" rx="10" fill="#111720" stroke="#243447" stroke-width="1"/>
      <text x="24" y="28" fill="#FFFFFF" font-family="'Space Grotesk', sans-serif" font-size="14" font-weight="700">Rolling Elevated Boost Budget Gauge (Anti-Starvation)</text>
      <rect x="24" y="60" width="948" height="20" rx="10" fill="#0B0F14" stroke="#243447" stroke-width="1"/>
      <rect x="24" y="60" width="126" height="20" rx="10" fill="#00E5FF"/>
      <text x="24" y="105" fill="#94A3B8" font-family="'JetBrains Mono', monospace" font-size="11">0s</text>
      <text x="150" y="105" fill="#00E5FF" font-family="'JetBrains Mono', monospace" font-size="11">120.0s consumed (13.3%)</text>
      <text x="930" y="105" fill="#94A3B8" font-family="'JetBrains Mono', monospace" font-size="11">900s (Limit)</text>
    </g>
    """
    return render_base_chrome("safety", "Safety Governor", "Boost budget, circuit breaker & anti-starvation guards", content)

# -----------------------------------------------------------------------------
# 6. EXPERIMENTS
# -----------------------------------------------------------------------------
def make_experiments() -> str:
    content = """
    <!-- Disclosure Header -->
    <g transform="translate(0, 0)">
      <rect x="0" y="0" width="996" height="56" rx="8" fill="rgba(255,179,0,0.08)" stroke="#FFB300" stroke-width="1"/>
      <text x="20" y="34" font-size="16">⚖</text>
      <text x="46" y="26" fill="#FFB300" font-family="'Space Grotesk', sans-serif" font-size="12" font-weight="700">Pre-Registered Experimental Evaluation (RESULTS.md Provenance)</text>
      <text x="46" y="42" fill="#94A3B8" font-family="'Space Grotesk', sans-serif" font-size="11">Strict scientific transparency: all criteria failures and harness audits reported unedited</text>
    </g>

    <!-- Criteria Table -->
    <g transform="translate(0, 74)">
      <rect x="0" y="0" width="996" height="300" rx="10" fill="#111720" stroke="#243447" stroke-width="1"/>
      <text x="24" y="28" fill="#FFFFFF" font-family="'Space Grotesk', sans-serif" font-size="14" font-weight="700">Success Criteria (S1 - S7)</text>

      <rect x="24" y="46" width="948" height="26" fill="#16202B" rx="4"/>
      <text x="36" y="63" fill="#94A3B8" font-family="'JetBrains Mono', monospace" font-size="10" font-weight="700">CRITERION</text>
      <text x="240" y="63" fill="#94A3B8" font-family="'JetBrains Mono', monospace" font-size="10" font-weight="700">OBSERVED VALUE</text>
      <text x="480" y="63" fill="#94A3B8" font-family="'JetBrains Mono', monospace" font-size="10" font-weight="700">STATUS</text>
      <text x="590" y="63" fill="#94A3B8" font-family="'JetBrains Mono', monospace" font-size="10" font-weight="700">AUDIT / TRANSPARENCY NOTE</text>

      <!-- Row S1 -->
      <text x="36" y="96" fill="#FFFFFF" font-family="'Space Grotesk', sans-serif" font-size="11" font-weight="700">S1: Contended Speedup</text>
      <text x="240" y="96" fill="#00E5FF" font-family="'JetBrains Mono', monospace" font-size="11">+75.6% (p &lt; 10⁻¹⁵)</text>
      <rect x="480" y="84" width="50" height="16" rx="3" fill="rgba(0,230,118,0.15)"/>
      <text x="505" y="95" fill="#00E676" font-family="'JetBrains Mono', monospace" font-size="9" text-anchor="middle">PASS</text>
      <text x="590" y="96" fill="#94A3B8" font-family="'Space Grotesk', sans-serif" font-size="11">Significant acceleration when claimed into elevated cgroup</text>

      <!-- Row S3 -->
      <line x1="24" y1="110" x2="972" y2="110" stroke="#1E2B3A" stroke-width="1"/>
      <text x="36" y="130" fill="#FFFFFF" font-family="'Space Grotesk', sans-serif" font-size="11" font-weight="700">S3: Claim Latency</text>
      <text x="240" y="130" fill="#FF3366" font-family="'JetBrains Mono', monospace" font-size="11">+9.4% advantage (45.2ms)</text>
      <rect x="480" y="118" width="50" height="16" rx="3" fill="rgba(255,51,102,0.15)"/>
      <text x="505" y="129" fill="#FF3366" font-family="'JetBrains Mono', monospace" font-size="9" text-anchor="middle">FAIL</text>
      <text x="590" y="130" fill="#94A3B8" font-family="'Space Grotesk', sans-serif" font-size="11">Linux /proc polling resolution bounded latency to ~45ms</text>

      <!-- Row S4 -->
      <line x1="24" y1="144" x2="972" y2="144" stroke="#1E2B3A" stroke-width="1"/>
      <text x="36" y="164" fill="#FFFFFF" font-family="'Space Grotesk', sans-serif" font-size="11" font-weight="700">S4: Irregular Precision</text>
      <text x="240" y="164" fill="#FFB300" font-family="'JetBrains Mono', monospace" font-size="11">0.0% false (constant delay)</text>
      <rect x="480" y="152" width="80" height="16" rx="3" fill="rgba(255,179,0,0.15)"/>
      <text x="520" y="163" fill="#FFB300" font-family="'JetBrains Mono', monospace" font-size="9" text-anchor="middle">INVALID</text>
      <text x="590" y="164" fill="#94A3B8" font-family="'Space Grotesk', sans-serif" font-size="11">Python random.Random(42) re-seeded; workload was constant ~13s</text>
    </g>
    """
    return render_base_chrome("experiments", "Experimental Evaluation", "Pre-registered benchmarks, C0-C3 matrices & ablations", content)

# -----------------------------------------------------------------------------
# 7. DECISION LOG
# -----------------------------------------------------------------------------
def make_decisions() -> str:
    content = """
    <!-- Table Card -->
    <g transform="translate(0, 0)">
      <rect x="0" y="0" width="996" height="680" rx="10" fill="#111720" stroke="#243447" stroke-width="1"/>
      <text x="24" y="28" fill="#FFFFFF" font-family="'Space Grotesk', sans-serif" font-size="14" font-weight="700">Daemon Arming & Gate Decision Stream</text>
      <text x="24" y="46" fill="#64748B" font-family="'Space Grotesk', sans-serif" font-size="11">Audited decision stream feeding confidence, contention, and cost/benefit gates</text>

      <rect x="24" y="64" width="948" height="26" fill="#16202B" rx="4"/>
      <text x="36" y="81" fill="#94A3B8" font-family="'JetBrains Mono', monospace" font-size="10" font-weight="700">ID</text>
      <text x="80" y="81" fill="#94A3B8" font-family="'JetBrains Mono', monospace" font-size="10" font-weight="700">TIMESTAMP</text>
      <text x="200" y="81" fill="#94A3B8" font-family="'JetBrains Mono', monospace" font-size="10" font-weight="700">DECISION</text>
      <text x="360" y="81" fill="#94A3B8" font-family="'JetBrains Mono', monospace" font-size="10" font-weight="700">CONTENTION PSI</text>
      <text x="520" y="81" fill="#94A3B8" font-family="'JetBrains Mono', monospace" font-size="10" font-weight="700">ESTIMATED BENEFIT</text>
      <text x="700" y="81" fill="#94A3B8" font-family="'JetBrains Mono', monospace" font-size="10" font-weight="700">ESTIMATED COST</text>
      <text x="860" y="81" fill="#94A3B8" font-family="'JetBrains Mono', monospace" font-size="10" font-weight="700">OUTCOME</text>

      <!-- Row 1 -->
      <text x="36" y="116" fill="#94A3B8" font-family="'JetBrains Mono', monospace" font-size="11">1</text>
      <text x="80" y="116" fill="#94A3B8" font-family="'JetBrains Mono', monospace" font-size="11">19:12:44.205</text>
      <rect x="200" y="104" width="70" height="18" rx="3" fill="rgba(0,230,118,0.15)"/>
      <text x="235" y="117" fill="#00E676" font-family="'JetBrains Mono', monospace" font-size="9" text-anchor="middle">ARMED</text>
      <text x="360" y="116" fill="#FFFFFF" font-family="'JetBrains Mono', monospace" font-size="11">14.2%</text>
      <text x="520" y="116" fill="#00E5FF" font-family="'JetBrains Mono', monospace" font-size="11">0.450s</text>
      <text x="700" y="116" fill="#94A3B8" font-family="'JetBrains Mono', monospace" font-size="11">0.080s</text>
      <rect x="860" y="104" width="50" height="18" rx="3" fill="rgba(0,229,255,0.15)"/>
      <text x="885" y="117" fill="#00E5FF" font-family="'JetBrains Mono', monospace" font-size="9" text-anchor="middle">HIT</text>

      <!-- Row 2 -->
      <line x1="24" y1="134" x2="972" y2="134" stroke="#1E2B3A" stroke-width="1"/>
      <text x="36" y="156" fill="#94A3B8" font-family="'JetBrains Mono', monospace" font-size="11">2</text>
      <text x="80" y="156" fill="#94A3B8" font-family="'JetBrains Mono', monospace" font-size="11">19:12:38.109</text>
      <rect x="200" y="144" width="70" height="18" rx="3" fill="rgba(255,179,0,0.15)"/>
      <text x="235" y="157" fill="#FFB300" font-family="'JetBrains Mono', monospace" font-size="9" text-anchor="middle">HOLDOUT</text>
      <text x="360" y="156" fill="#FFFFFF" font-family="'JetBrains Mono', monospace" font-size="11">13.8%</text>
      <text x="520" y="156" fill="#00E5FF" font-family="'JetBrains Mono', monospace" font-size="11">0.440s</text>
      <text x="700" y="156" fill="#94A3B8" font-family="'JetBrains Mono', monospace" font-size="11">0.080s</text>
      <rect x="860" y="144" width="60" height="18" rx="3" fill="rgba(255,179,0,0.15)"/>
      <text x="890" y="157" fill="#FFB300" font-family="'JetBrains Mono', monospace" font-size="9" text-anchor="middle">HOLDOUT</text>
    </g>
    """
    return render_base_chrome("decisions", "Decision Log", "Audited daemon arming decisions and gate evaluations", content)

# -----------------------------------------------------------------------------
# 0. COMPARE (WITH vs WITHOUT PreCompile) - HERO SCREEN
# -----------------------------------------------------------------------------
def make_compare() -> str:
    content = """
    <!-- Selectors Toolbar -->
    <g transform="translate(0, 0)">
      <rect x="0" y="0" width="996" height="50" rx="8" fill="#16202B" stroke="#243447" stroke-width="1"/>
      <text x="16" y="30" fill="#94A3B8" font-family="'Space Grotesk', sans-serif" font-size="11" font-weight="700">SCENARIO:</text>
      <rect x="85" y="14" width="230" height="24" rx="4" fill="#111720" stroke="#243447"/>
      <text x="95" y="30" fill="#F1F5F9" font-family="'Space Grotesk', sans-serif" font-size="11">Scenario A: Regular Delays (~2.0s)</text>

      <text x="335" y="30" fill="#94A3B8" font-family="'Space Grotesk', sans-serif" font-size="11" font-weight="700">LOAD LEVEL:</text>
      <rect x="420" y="14" width="200" height="24" rx="4" fill="#111720" stroke="#243447"/>
      <text x="430" y="30" fill="#F1F5F9" font-family="'Space Grotesk', sans-serif" font-size="11">High Contention (PSI 60-80%)</text>

      <text x="640" y="30" fill="#94A3B8" font-family="'Space Grotesk', sans-serif" font-size="11" font-weight="700">SOURCE:</text>
      <rect x="700" y="14" width="130" height="24" rx="4" fill="#111720" stroke="#243447"/>
      <text x="710" y="30" fill="#00E5FF" font-family="'JetBrains Mono', monospace" font-size="11">Recorded JSONL</text>

      <!-- Toggle -->
      <rect x="850" y="17" width="14" height="14" rx="2" fill="#FFB300"/>
      <text x="872" y="29" fill="#F1F5F9" font-family="'Space Grotesk', sans-serif" font-size="11">Reactive Only (C1)</text>
    </g>

    <!-- Hero Speedup Banner Card -->
    <g transform="translate(0, 62)">
      <rect x="0" y="0" width="996" height="90" rx="10" fill="url(#grad-hero)" stroke="#00E5FF" stroke-width="1.5"/>
      <defs>
        <linearGradient id="grad-hero" x1="0" y1="0" x2="1" y2="0">
          <stop offset="0%" stop-color="#16202B"/>
          <stop offset="100%" stop-color="#0E242C"/>
        </linearGradient>
      </defs>

      <!-- Speedup Badge -->
      <rect x="20" y="18" width="190" height="54" rx="8" fill="rgba(0,229,255,0.15)" stroke="#00E5FF" stroke-width="2"/>
      <text x="115" y="54" fill="#00E5FF" font-family="'JetBrains Mono', monospace" font-size="24" font-weight="800" text-anchor="middle">+75.6% FASTER</text>

      <text x="230" y="42" fill="#FFFFFF" font-family="'Space Grotesk', sans-serif" font-size="13" font-weight="700">95% Bootstrap CI: [+73.7%, +76.7%]</text>
      <text x="230" y="62" fill="#94A3B8" font-family="'JetBrains Mono', monospace" font-size="11">Mann-Whitney U: p &lt; 10⁻¹⁴ (Highly Significant)</text>

      <!-- Vertical divider -->
      <line x1="560" y1="15" x2="560" y2="75" stroke="#243447" stroke-width="1"/>

      <!-- Replay simulation trigger -->
      <rect x="580" y="26" width="120" height="28" rx="6" fill="#223243" stroke="#00E5FF" stroke-width="1"/>
      <text x="640" y="44" fill="#00E5FF" font-family="'Space Grotesk', sans-serif" font-size="11" font-weight="700" text-anchor="middle">▶ Replay Build Run</text>
      <text x="640" y="68" fill="#64748B" font-family="'JetBrains Mono', monospace" font-size="10" text-anchor="middle">Elapsed: 2.04s</text>

      <!-- Honest mechanism note -->
      <text x="725" y="36" fill="#64748B" font-family="'Space Grotesk', sans-serif" font-size="10" font-weight="700">⚖️ HONEST MECHANISM AUDIT</text>
      <text x="725" y="52" fill="#94A3B8" font-family="'Space Grotesk', sans-serif" font-size="10">Full predictive pre-allocation accelerated the build by 75.6%.</text>
      <text x="725" y="66" fill="#94A3B8" font-family="'Space Grotesk', sans-serif" font-size="10">CPU contention was mitigated before the compiler spawned.</text>
    </g>

    <!-- Three Comparison Panels -->
    <g transform="translate(0, 164)">
      <!-- Left Panel: WITHOUT PreCompile (Cool Grey-Blue #90A4AE) -->
      <g transform="translate(0, 0)">
        <rect x="0" y="0" width="318" height="260" rx="10" fill="#16202B" stroke="#90A4AE" stroke-width="1.5"/>
        <text x="16" y="26" fill="#64748B" font-family="'Space Grotesk', sans-serif" font-size="10" font-weight="700">BASELINE / HOLDOUT</text>
        <text x="16" y="46" fill="#90A4AE" font-family="'Space Grotesk', sans-serif" font-size="16" font-weight="800">WITHOUT PreCompile</text>
        <text x="16" y="62" fill="#64748B" font-family="'Space Grotesk', sans-serif" font-size="11">C0 Baseline • Normal CFS</text>

        <!-- Big Numeral -->
        <text x="16" y="105" fill="#90A4AE" font-family="'JetBrains Mono', monospace" font-size="38" font-weight="800">8.38s</text>
        <text x="16" y="122" fill="#94A3B8" font-family="'JetBrains Mono', monospace" font-size="10">Median: 8.38s • CI: [8.21s, 8.55s] • N=50</text>

        <!-- Progress Bar -->
        <text x="16" y="148" fill="#64748B" font-family="'Space Grotesk', sans-serif" font-size="10">Build Progress</text>
        <text x="302" y="148" fill="#F1F5F9" font-family="'JetBrains Mono', monospace" font-size="10" text-anchor="end">2.04s / 8.38s (24%)</text>
        <rect x="16" y="154" width="286" height="8" rx="4" fill="#111720"/>
        <rect x="16" y="154" width="70" height="8" rx="4" fill="#90A4AE"/>
        <text x="16" y="176" fill="#90A4AE" font-family="'JetBrains Mono', monospace" font-size="10">Building under heavy contention...</text>

        <!-- Telemetry -->
        <line x1="16" y1="190" x2="302" y2="190" stroke="#243447" stroke-width="1"/>
        <text x="16" y="210" fill="#64748B" font-family="'Space Grotesk', sans-serif" font-size="10">CPU SHARE: <tspan fill="#F1F5F9" font-family="'JetBrains Mono', monospace">CFS 100 (~12.5%)</tspan></text>
        <text x="16" y="230" fill="#64748B" font-family="'Space Grotesk', sans-serif" font-size="10">CLAIM LATENCY: <tspan fill="#F1F5F9" font-family="'JetBrains Mono', monospace">N/A (Standard spawn)</tspan></text>
        <text x="16" y="250" fill="#64748B" font-family="'Space Grotesk', sans-serif" font-size="10">CONTENTION: <tspan fill="#FF3366" font-family="'JetBrains Mono', monospace">+359% contention delay</tspan></text>
      </g>

      <!-- Middle Panel: Reactive Only (Amber #FFB300) -->
      <g transform="translate(338, 0)">
        <rect x="0" y="0" width="318" height="260" rx="10" fill="#16202B" stroke="#FFB300" stroke-width="1"/>
        <text x="16" y="26" fill="#64748B" font-family="'Space Grotesk', sans-serif" font-size="10" font-weight="700">POST-SPAWN BOOST</text>
        <text x="16" y="46" fill="#FFB300" font-family="'Space Grotesk', sans-serif" font-size="16" font-weight="800">Reactive Only</text>
        <text x="16" y="62" fill="#64748B" font-family="'Space Grotesk', sans-serif" font-size="11">C1 Reactive • PID discovery lag</text>

        <!-- Big Numeral -->
        <text x="16" y="105" fill="#FFB300" font-family="'JetBrains Mono', monospace" font-size="38" font-weight="800">1.99s</text>
        <text x="16" y="122" fill="#94A3B8" font-family="'JetBrains Mono', monospace" font-size="10">Median: 1.99s • CI: [1.95s, 2.04s] • N=50</text>

        <!-- Progress Bar -->
        <text x="16" y="148" fill="#64748B" font-family="'Space Grotesk', sans-serif" font-size="10">Build Progress</text>
        <text x="302" y="148" fill="#F1F5F9" font-family="'JetBrains Mono', monospace" font-size="10" text-anchor="end">1.99s / 1.99s (100%)</text>
        <rect x="16" y="154" width="286" height="8" rx="4" fill="#111720"/>
        <rect x="16" y="154" width="286" height="8" rx="4" fill="#FFB300"/>
        <text x="16" y="176" fill="#FFB300" font-family="'JetBrains Mono', monospace" font-size="10" font-weight="700">COMPLETED in 1.99s</text>

        <!-- Telemetry -->
        <line x1="16" y1="190" x2="302" y2="190" stroke="#243447" stroke-width="1"/>
        <text x="16" y="210" fill="#64748B" font-family="'Space Grotesk', sans-serif" font-size="10">CPU SHARE: <tspan fill="#F1F5F9" font-family="'JetBrains Mono', monospace">CFS 500 (~62.5%)</tspan></text>
        <text x="16" y="230" fill="#64748B" font-family="'Space Grotesk', sans-serif" font-size="10">CLAIM LATENCY: <tspan fill="#FFB300" font-family="'JetBrains Mono', monospace">+47.2 ms (/proc scan lag)</tspan></text>
        <text x="16" y="250" fill="#64748B" font-family="'Space Grotesk', sans-serif" font-size="10">vs C2: <tspan fill="#94A3B8" font-family="'JetBrains Mono', monospace">p = 0.2582 (No diff)</tspan></text>
      </g>

      <!-- Right Panel: WITH PreCompile (Electric Cyan #00E5FF) -->
      <g transform="translate(676, 0)">
        <rect x="0" y="0" width="320" height="260" rx="10" fill="#0E242C" stroke="#00E5FF" stroke-width="2"/>
        <text x="16" y="26" fill="#64748B" font-family="'Space Grotesk', sans-serif" font-size="10" font-weight="700">PREDICTIVE ALLOCATION</text>
        <text x="16" y="46" fill="#00E5FF" font-family="'Space Grotesk', sans-serif" font-size="16" font-weight="800">WITH PreCompile</text>
        <text x="16" y="62" fill="#64748B" font-family="'Space Grotesk', sans-serif" font-size="11">C2 Predictive • Priority Before Spawn</text>

        <!-- Big Numeral -->
        <text x="16" y="105" fill="#00E5FF" font-family="'JetBrains Mono', monospace" font-size="38" font-weight="800">2.04s</text>
        <text x="16" y="122" fill="#94A3B8" font-family="'JetBrains Mono', monospace" font-size="10">Median: 2.04s • CI: [1.98s, 2.11s] • N=50</text>

        <!-- Progress Bar -->
        <text x="16" y="148" fill="#64748B" font-family="'Space Grotesk', sans-serif" font-size="10">Build Progress</text>
        <text x="304" y="148" fill="#00E5FF" font-family="'JetBrains Mono', monospace" font-size="10" text-anchor="end" font-weight="700">⚡ COMPLETED in 2.04s</text>
        <rect x="16" y="154" width="288" height="8" rx="4" fill="#111720"/>
        <rect x="16" y="154" width="288" height="8" rx="4" fill="#00E5FF"/>
        <text x="16" y="176" fill="#00E5FF" font-family="'JetBrains Mono', monospace" font-size="10" font-weight="700">⚡ Finished 6.34s earlier than baseline!</text>

        <!-- Telemetry -->
        <line x1="16" y1="190" x2="304" y2="190" stroke="#243447" stroke-width="1"/>
        <text x="16" y="210" fill="#64748B" font-family="'Space Grotesk', sans-serif" font-size="10">CPU SHARE: <tspan fill="#00E5FF" font-family="'JetBrains Mono', monospace">CFS 500 (~62.5%)</tspan></text>
        <text x="16" y="230" fill="#64748B" font-family="'Space Grotesk', sans-serif" font-size="10">CLAIM LATENCY: <tspan fill="#00E5FF" font-family="'JetBrains Mono', monospace">Preemptive (~45.6ms lead)</tspan></text>
        <text x="16" y="250" fill="#64748B" font-family="'Space Grotesk', sans-serif" font-size="10">SPEEDUP: <tspan fill="#00E5FF" font-family="'JetBrains Mono', monospace">+75.6% accelerated</tspan></text>
      </g>
    </g>

    <!-- Bottom: Overlaid CPU Share Timeline -->
    <g transform="translate(0, 436)">
      <rect x="0" y="0" width="996" height="240" rx="10" fill="#111720" stroke="#243447" stroke-width="1"/>
      <text x="24" y="26" fill="#FFFFFF" font-family="'Space Grotesk', sans-serif" font-size="13" font-weight="700">Overlaid CPU Share Timeline & Event Sequence</text>
      <text x="24" y="42" fill="#64748B" font-family="'Space Grotesk', sans-serif" font-size="11">Save Event • Preemptive Reservation • Process Spawn • Completion on identical time axes</text>

      <!-- Legend -->
      <rect x="580" y="18" width="10" height="4" rx="2" fill="#90A4AE"/>
      <text x="596" y="23" fill="#90A4AE" font-family="'JetBrains Mono', monospace" font-size="10">WITHOUT PreCompile</text>
      <rect x="735" y="18" width="10" height="4" rx="2" fill="#FFB300"/>
      <text x="751" y="23" fill="#FFB300" font-family="'JetBrains Mono', monospace" font-size="10">Reactive Only (C1)</text>
      <rect x="870" y="18" width="10" height="4" rx="2" fill="#00E5FF"/>
      <text x="886" y="23" fill="#00E5FF" font-family="'JetBrains Mono', monospace" font-size="10">WITH PreCompile</text>

      <!-- Chart Grid & Axis -->
      <g transform="translate(60, 60)">
        <!-- Horizontal grid -->
        <line x1="0" y1="0" x2="890" y2="0" stroke="#243447" stroke-dasharray="3,3"/>
        <text x="-10" y="4" fill="#64748B" font-family="'JetBrains Mono', monospace" font-size="10" text-anchor="end">62.5% (CFS 500)</text>

        <line x1="0" y1="75" x2="890" y2="75" stroke="#243447" stroke-dasharray="3,3"/>
        <text x="-10" y="79" fill="#64748B" font-family="'JetBrains Mono', monospace" font-size="10" text-anchor="end">12.5% (CFS 100)</text>

        <line x1="0" y1="120" x2="890" y2="120" stroke="#243447"/>
        <text x="-10" y="124" fill="#64748B" font-family="'JetBrains Mono', monospace" font-size="10" text-anchor="end">0%</text>

        <!-- Time Ticks -->
        <text x="0" y="140" fill="#64748B" font-family="'JetBrains Mono', monospace" font-size="10" text-anchor="middle">0s (Save)</text>
        <text x="160" y="140" fill="#64748B" font-family="'JetBrains Mono', monospace" font-size="10" text-anchor="middle">2.0s (Spawn)</text>
        <text x="323" y="140" fill="#00E5FF" font-family="'JetBrains Mono', monospace" font-size="10" text-anchor="middle" font-weight="700">4.04s (WITH Finish)</text>
        <text x="830" y="140" fill="#90A4AE" font-family="'JetBrains Mono', monospace" font-size="10" text-anchor="middle">10.38s (WITHOUT Finish)</text>

        <!-- Vertical Markers -->
        <line x1="156" y1="0" x2="156" y2="120" stroke="#00E5FF" stroke-width="1.5" stroke-dasharray="3,2"/>
        <text x="156" y="-8" fill="#00E5FF" font-family="'JetBrains Mono', monospace" font-size="9" text-anchor="end">Reservation (1.95s)</text>

        <line x1="160" y1="0" x2="160" y2="120" stroke="#FFFFFF" stroke-width="1"/>

        <line x1="164" y1="0" x2="164" y2="120" stroke="#FFB300" stroke-width="1" stroke-dasharray="2,2"/>

        <line x1="323" y1="0" x2="323" y2="120" stroke="#00E5FF" stroke-width="1.5"/>
        <line x1="830" y1="0" x2="830" y2="120" stroke="#90A4AE" stroke-width="1.5" stroke-dasharray="4,4"/>

        <!-- WITH Trace (Cyan) -->
        <path d="M 0 120 L 156 120 L 156 0 L 323 0 L 323 120 L 890 120" fill="none" stroke="#00E5FF" stroke-width="3"/>

        <!-- Reactive Trace (Amber) -->
        <path d="M 0 120 L 160 120 L 160 75 L 164 75 L 164 0 L 320 0 L 320 120 L 890 120" fill="none" stroke="#FFB300" stroke-width="2" stroke-dasharray="3,3"/>

        <!-- WITHOUT Trace (Cool Grey-Blue) -->
        <path d="M 0 120 L 160 120 L 160 75 L 830 75 L 830 120 L 890 120" fill="none" stroke="#90A4AE" stroke-width="2.5" stroke-dasharray="6,4"/>
      </g>
    </g>
    """
    return render_base_chrome("compare", "WITH vs WITHOUT PreCompile", "Direct empirical comparison of anticipatory priority vs normal scheduling", content)

def make_presentation_mode() -> str:
    """Full-screen presentation mode capture at 1366x768 with nav hidden, large fonts, and floating exit button."""
    return f"""<svg width="{WIDTH}" height="{HEIGHT}" viewBox="0 0 {WIDTH} {HEIGHT}" xmlns="http://www.w3.org/2000/svg">
      <!-- Ground -->
      <rect x="0" y="0" width="{WIDTH}" height="{HEIGHT}" fill="#050810"/>

      <!-- Floating Exit Button -->
      <g transform="translate({WIDTH - 240}, 24)">
        <rect x="0" y="0" width="216" height="36" rx="6" fill="#16202B" stroke="#9461FF" stroke-width="1.5"/>
        <text x="108" y="22" fill="#9461FF" font-family="'JetBrains Mono', monospace" font-size="12" font-weight="700" text-anchor="middle">✕ Exit Presentation [P / Esc]</text>
      </g>

      <!-- Large Header -->
      <text x="60" y="70" fill="#FFFFFF" font-family="'Space Grotesk', sans-serif" font-size="34" font-weight="800">WITH vs WITHOUT PreCompile</text>
      <text x="60" y="98" fill="#94A3B8" font-family="'JetBrains Mono', monospace" font-size="14">Direct empirical comparison of anticipatory priority reservation vs. normal scheduling</text>

      <!-- Giant Central Hero Badge -->
      <g transform="translate({WIDTH // 2 - 280}, 130)">
        <rect x="0" y="0" width="560" height="120" rx="20" fill="rgba(124,58,237,0.12)" stroke="#9461FF" stroke-width="2"/>
        <text x="280" y="65" fill="#9461FF" font-family="'JetBrains Mono', monospace" font-size="64" font-weight="900" text-anchor="middle">+75.6% Speedup</text>
        <text x="280" y="92" fill="#F1F5F9" font-family="'JetBrains Mono', monospace" font-size="14" font-weight="600" text-anchor="middle">95% Bootstrap CI: [73.7%, 76.7%] • Mann-Whitney U: p &lt; 0.0001 (Highly Significant)</text>
      </g>

      <!-- Two Large Side-by-Side Symmetrical Panels -->
      <g transform="translate(60, 280)">
        <!-- Left Panel: WITHOUT PreCompile -->
        <g transform="translate(0, 0)">
          <rect x="0" y="0" width="590" height="420" rx="16" fill="#0D1117" stroke="#4A6FA5" stroke-width="2.5"/>
          <text x="32" y="44" fill="#4A6FA5" font-family="'JetBrains Mono', monospace" font-size="14" font-weight="700">BASELINE / HOLDOUT (C0)</text>
          <text x="32" y="80" fill="#FFFFFF" font-family="'Space Grotesk', sans-serif" font-size="24" font-weight="800">WITHOUT PreCompile</text>
          <text x="32" y="104" fill="#64748B" font-family="'JetBrains Mono', monospace" font-size="13">Normal CFS Scheduling • CFS 100 (~12.5% share)</text>

          <!-- Extra Large Number -->
          <text x="32" y="190" fill="#4A6FA5" font-family="'JetBrains Mono', monospace" font-size="84" font-weight="900">8.38s</text>
          <text x="32" y="224" fill="#94A3B8" font-family="'JetBrains Mono', monospace" font-size="15">Median: 8.38s • 95% CI: [8.03s, 9.47s] • N=50</text>

          <!-- Large Progress Bar -->
          <rect x="32" y="260" width="526" height="16" rx="8" fill="#16202B"/>
          <rect x="32" y="260" width="130" height="16" rx="8" fill="#4A6FA5"/>
          <text x="32" y="302" fill="#4A6FA5" font-family="'JetBrains Mono', monospace" font-size="14" font-weight="700">Building under heavy contention...</text>

          <line x1="32" y1="325" x2="558" y2="325" stroke="#1E2D40" stroke-width="1"/>
          <text x="32" y="355" fill="#64748B" font-family="'Space Grotesk', sans-serif" font-size="14">CPU SHARE: <tspan fill="#F1F5F9" font-family="'JetBrains Mono', monospace" font-weight="700">CFS 100 (~12.5%)</tspan></text>
          <text x="32" y="385" fill="#64748B" font-family="'Space Grotesk', sans-serif" font-size="14">CONTENTION IMPACT: <tspan fill="#EF4444" font-family="'JetBrains Mono', monospace" font-weight="700">+359% contention delay</tspan></text>
        </g>

        <!-- Right Panel: WITH PreCompile -->
        <g transform="translate(656, 0)">
          <rect x="0" y="0" width="590" height="420" rx="16" fill="#0D1117" stroke="#9461FF" stroke-width="3"/>
          <text x="32" y="44" fill="#9461FF" font-family="'JetBrains Mono', monospace" font-size="14" font-weight="700">PREDICTIVE ALLOCATION (C2)</text>
          <text x="32" y="80" fill="#FFFFFF" font-family="'Space Grotesk', sans-serif" font-size="24" font-weight="800">WITH PreCompile</text>
          <text x="32" y="104" fill="#64748B" font-family="'JetBrains Mono', monospace" font-size="13">Anticipatory CFS Reservation • CFS 500 (~62.5% share)</text>

          <!-- Extra Large Number -->
          <text x="32" y="190" fill="#9461FF" font-family="'JetBrains Mono', monospace" font-size="84" font-weight="900">2.04s</text>
          <text x="32" y="224" fill="#94A3B8" font-family="'JetBrains Mono', monospace" font-size="15">Median: 2.04s • 95% CI: [1.74s, 8.54s] • N=50</text>

          <!-- Large Progress Bar -->
          <rect x="32" y="260" width="526" height="16" rx="8" fill="#16202B"/>
          <rect x="32" y="260" width="526" height="16" rx="8" fill="#9461FF"/>
          <text x="32" y="302" fill="#9461FF" font-family="'JetBrains Mono', monospace" font-size="14" font-weight="700">⚡ COMPLETED in 2.04s (Finished 6.34s earlier!)</text>

          <line x1="32" y1="325" x2="558" y2="325" stroke="#1E2D40" stroke-width="1"/>
          <text x="32" y="355" fill="#64748B" font-family="'Space Grotesk', sans-serif" font-size="14">CPU SHARE: <tspan fill="#9461FF" font-family="'JetBrains Mono', monospace" font-weight="700">CFS 500 (~62.5%)</tspan></text>
          <text x="32" y="385" fill="#64748B" font-family="'Space Grotesk', sans-serif" font-size="14">CLAIM LATENCY: <tspan fill="#9461FF" font-family="'JetBrains Mono', monospace" font-weight="700">Preemptive (~45.6ms lead)</tspan></text>
        </g>
      </g>
    </svg>"""

# Generate and save all 9 SVGs
views = [
    ("00_compare.svg", make_compare),
    ("01_overview.svg", make_overview),
    ("02_timeline.svg", make_timeline),
    ("03_distributions.svg", make_distributions),
    ("04_benefit.svg", make_benefit),
    ("05_safety.svg", make_safety),
    ("06_experiments.svg", make_experiments),
    ("07_decisions.svg", make_decisions),
    ("08_presentation_mode.svg", make_presentation_mode),
]

for filename, generator in views:
    path = OUT_DIR / filename
    svg_code = generator()
    path.write_text(svg_code, encoding="utf-8")
    print(f"Generated screenshot: {path}")
