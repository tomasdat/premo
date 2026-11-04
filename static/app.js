const $ = (s) => document.querySelector(s);
const view = $("#view");
let tab = "squads";
let players = [];
let selPlayer = null;
let selSquad = null; // {key, title, ids, exact}
let me = null;
let loginShown = false;

const api = async (path, opts = {}) => {
  const r = await fetch("/api" + path, {
    headers: { "Content-Type": "application/json" }, ...opts,
    body: opts.body ? JSON.stringify(opts.body) : undefined,
  });
  if (r.status === 401 && !path.startsWith("/auth/")) { showLogin(); throw new Error("Not signed in"); }
  if (!r.ok) {
    let msg = r.statusText;
    try { msg = (await r.json()).detail || msg; } catch {}
    throw new Error(msg);
  }
  return r.json();
};
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const queue = () => $("#queue").value;
const flexNote = (n) => (queue() !== "440" ? "" : n === 4 ? "Flex has no 4-stacks, so these were 5-stacks with one outside player." : n === 1 || n === 2 ? "Flex is usually played as a 3- or 5-stack, so this is mostly solo/duo games." : "");
const maxSquad = () => (queue() === "420" ? 2 : 5);
const q = () => (queue() ? `queue=${queue()}` : "");
const pname = (id) => esc((players.find((p) => p.id === id) || {}).name || "?");
const ROLE = { TOP: "Top", JUNGLE: "Jungle", MIDDLE: "Mid", BOTTOM: "Bot", UTILITY: "Support" };

/* ---------- Data Dragon (icons) ---------- */
let dd = { v: "14.24.1", spells: {}, runes: {} };
const ddBase = () => `https://ddragon.leagueoflegends.com/cdn/${dd.v}/img`;
async function loadDDragon() {
  try {
    dd.v = (await (await fetch("https://ddragon.leagueoflegends.com/api/versions.json")).json())[0];
    const sp = await (await fetch(`https://ddragon.leagueoflegends.com/cdn/${dd.v}/data/en_US/summoner.json`)).json();
    for (const s of Object.values(sp.data)) dd.spells[s.key] = s.image.full;
    const ru = await (await fetch(`https://ddragon.leagueoflegends.com/cdn/${dd.v}/data/en_US/runesReforged.json`)).json();
    for (const st of ru) {
      dd.runes[st.id] = st.icon;
      for (const slot of st.slots) for (const r of slot.runes) dd.runes[r.id] = r.icon;
    }
  } catch (e) { console.warn("Data Dragon unavailable", e); }
}
const ico = (url, title = "", cls = "") => url
  ? `<img class="ico ${cls}" title="${esc(title)}" src="${url}" onerror="this.style.visibility='hidden'">`
  : `<span class="ico ${cls}"></span>`;
const DD_CHAMP = { FiddleSticks: "Fiddlesticks" };
const champIco = (c, cls = "") => ico(`${ddBase()}/champion/${encodeURIComponent(DD_CHAMP[c] || c)}.png`, c, cls);
const spellIco = (id) => ico(dd.spells[id] && `${ddBase()}/spell/${dd.spells[id]}`);
const runeIco = (id) => ico(dd.runes[id] && `https://ddragon.leagueoflegends.com/cdn/img/${dd.runes[id]}`, "", "round");
const itemsHtml = (items = []) => {
  const cell = (i) => (i ? ico(`${ddBase()}/item/${i}.png`) : `<span class="ico empty"></span>`);
  return `<span class="items"><span class="g">${items.slice(0, 6).map(cell).join("")}</span>${cell(items[6])}</span>`;
};
const build = (m) => `<span class="spells">${spellIco(m.spell1)}${spellIco(m.spell2)}</span><span class="spells">${runeIco(m.keystone)}${runeIco(m.sub_style)}</span>`;

/* ---------- helpers ---------- */
const ago = (ms) => {
  const s = (Date.now() - ms) / 1000;
  if (s < 3600) return `${Math.max(1, Math.floor(s / 60))} min ago`;
  if (s < 86400) return `${Math.floor(s / 3600)} hours ago`;
  if (s < 86400 * 30) { const d = Math.floor(s / 86400); return `${d} day${d > 1 ? "s" : ""} ago`; }
  return new Date(ms).toLocaleDateString();
};
const mins = (s) => `${Math.floor(s / 60)}:${String(s % 60).padStart(2, "0")}`;
const wcls = (w) => (w >= 50 ? "win" : "loss");
const donut = (wr, size = 64, games = 1) => `<div class="donut ${games ? "" : "empty-d"}" style="--p:${wr};--s:${size}px"><span>${games ? Math.round(wr) + "%" : "–"}</span></div>`;
const kdaRatio = (k, d, a) => (d === 0 ? "Perfect" : ((k + a) / d).toFixed(2));
const tblock = (title, inner) => `<div class="card"><h3>${title}</h3>${inner}</div>`;
const none = (t) => `<div class="nodata">${t}</div>`;

function table(head, rows) {
  return `<table><tr>${head.map((h) => `<th>${h}</th>`).join("")}</tr>${rows.map((r) => `<tr>${r.map((c) => `<td>${c}</td>`).join("")}</tr>`).join("")}</table>`;
}

/* ---------- match rows ---------- */
const gname = (n) => (n || "?").split("#")[0];
const diffName = (tracked, name) => tracked && tracked.toLowerCase() !== gname(name).toLowerCase();
const who = (tracked, name) => esc(gname(name)) + (diffName(tracked, name) ? ` <span class="mut">(${esc(tracked)})</span>` : "");

// Difficulty = opponents' average rank minus own team's, in divisions (+ = harder opponents).
const RK_ABBR = { Iron: "I", Bronze: "B", Silver: "S", Gold: "G", Platinum: "P", Emerald: "E", Diamond: "D", Master: "M", Grandmaster: "GM", Challenger: "C" };
const RK_DIV = { IV: 4, III: 3, II: 2, I: 1 };
// "Emerald IV" -> colored badge "E4"; "Master+ 120 LP" -> "M+ 120"
const rk = (s) => {
  if (!s) return "";
  const m = /^(\w+)(\+)? ?(IV|III|II|I|\d+ LP)?$/.exec(String(s).trim());
  if (!m || !RK_ABBR[m[1]] && m[1] !== "Master") return esc(s);
  const t = m[1], ab = RK_ABBR[t] || "M";
  const tail = m[2] ? "+" + (m[3] ? " " + m[3].replace(" LP", "") : "") : RK_DIV[m[3]] || "";
  return `<span class="rk t-${t.toLowerCase()}" title="${esc(s)}">${ab}${tail}</span>`;
};
const dsign = (v) => (v > 0 ? "+" : "") + v;
const dcls = (v) => (v >= 0.5 ? "hard" : v <= -0.5 ? "easy" : "even");
const dtitle = (d) => `Opponents avg ${d.opp_label} vs your team ${d.own_label} (${dsign(d.diff)} divisions). Highest ranked: opponents ${d.opp_top}, your team ${d.own_top}`;
const dchip = (d) => (d ? `<span class="dchip ${dcls(d.diff)}" title="${esc(dtitle(d))}">Opp ${rk(d.opp_label)} · top ${rk(d.opp_top)}</span>` : "");
const eloChip = (e) => (e ? `<span class="elo" title="Current rank average: ${esc(Object.entries(e.players).map(([id, r]) => pname(+id) + " " + r).join(", "))}${e.known < e.size ? ` (${e.known} of ${e.size} ranked)` : ""}">Squad avg ${rk(e.avg)}</span>` : "");
const davg = (d) => (d ? `<span class="dchip ${dcls(d.diff)}" title="Average over ${d.known} of ${d.games} games with known ranks. Opponents ${esc(d.opp_label)} vs your team ${esc(d.own_label)}">Opp avg ${rk(d.opp_label)} (${dsign(d.diff)})</span>` : "");

function teamBlock(ps, d, maxDmg, focus) {
  const win = ps[0].win;
  const td = (d.difficulty && d.difficulty[ps[0].team_id]) || {};
  const teamRank = td.own_label ? `${rk(td.own_label)} · top ${rk(td.own_top)}` : "";
  const kills = ps.reduce((s, p) => s + p.kills, 0), deaths = ps.reduce((s, p) => s + p.deaths, 0);
  const mins = (d.duration || 1) / 60;
  const k = (n) => (n >= 1000 ? (n / 1000).toFixed(1) + "k" : n);
  return `<div class="tblock ${win ? "win" : "loss"}">
    <div class="trow thead"><span class="${win ? "win" : "loss"} tres">${win ? "Victory" : "Defeat"} <span class="mut">(${ps[0].team_id === 100 ? "Blue" : "Red"} Team)</span>${teamRank ? ` <span class="mut">· avg ${teamRank}</span>` : ""}</span>
      <span>Score</span><span>KDA</span><span>Damage</span><span>Gold</span><span>CS</span><span>Wards</span><span>Items</span></div>
    ${ps.map((p) => `<div class="trow ${(focus ? p.tracked_player === focus : p.tracked_player) ? "tr" : ""}">
      <span class="tid"><span class="lvl">${champIco(p.champion, "lg2")}<i>${p.level}</i></span>${build(p)}
        <span class="tname"><b class="${(focus ? p.tracked_player === focus : p.tracked_player) ? "tracked" : ""}">${esc(gname(p.name))}</b>
          <span class="mut">${[rk(p.rank), diffName(p.tracked_player, p.name) ? esc(p.tracked_player) : ""].filter(Boolean).join(" · ")}</span>${(p.titles || []).length ? `<span class="ttl">${p.titles.map((x) => `<i class="t-${x.key}" title="${esc(x.tip)}">${x.label}</i>`).join("")}</span>` : ""}</span></span>
      <span class="tsc">${p.score == null ? "" : `<b class="${p.tag ? p.tag.toLowerCase() : ""}">${p.score.toFixed(1)}</b>${p.tag ? `<i class="${p.tag.toLowerCase()}">${p.tag}</i>` : ""}`}</span>
      <span class="tkda"><b>${p.kills} / ${p.deaths} / ${p.assists}</b><span class="mut">${p.deaths ? ((p.kills + p.assists) / p.deaths).toFixed(2) : "Perfect"} KDA</span></span>
      <span class="tdmg"><b>${p.damage.toLocaleString()}</b><span class="dbar"><i style="width:${Math.round(100 * p.damage / maxDmg)}%"></i></span></span>
      <span>${k(p.gold)}</span><span>${p.cs}<span class="mut"> (${(p.cs / mins).toFixed(1)})</span></span><span>${p.vision}</span>
      ${itemsHtml(p.items)}</div>`).join("")}</div>`;
}
function playerCol(list, selfPuuid) {
  return `<div>${list.map((p) => `<div class="pl ${p.tracked ? "tr" : ""} ${p.puuid === selfPuuid ? "me" : ""}">${champIco(p.champion, "sm")}<span${diffName(p.tracked, p.name) ? ` title="${esc(p.tracked)}"` : ""}>${esc(gname(p.name))}</span></div>`).join("")}</div>`;
}

function matchRow(m) {
  const t1 = (m.players || []).filter((p) => p.team_id === 100);
  const t2 = (m.players || []).filter((p) => p.team_id !== 100);
  const cspm = m.duration ? (m.cs / (m.duration / 60)).toFixed(1) : "?";
  return `<div class="mrow ${m.win ? "win" : "loss"}" data-mid="${esc(m.match_id)}">
    <div class="mmeta"><span class="qn">${esc(m.queue)}</span><span class="mut">${ago(m.game_creation)}</span>
      <span class="res">${m.win ? "Victory" : "Defeat"}</span> <span class="mut">${mins(m.duration)}</span>${dchip(m.difficulty)}</div>
    <div class="champ">${champIco(m.champion, "lg")}<span class="lvl">${m.level ?? "?"}</span></div>
    ${build(m)}
    <div class="kdablk"><div class="k">${m.kills} / <i>${m.deaths}</i> / ${m.assists}</div>
      <div class="r">${kdaRatio(m.kills, m.deaths, m.assists)} KDA</div>
      <div class="r">${m.cs} CS (${cspm}) · ${m.vision} vis</div></div>
    ${itemsHtml(m.items)}
    <div class="teams">${playerCol(t1, m.self_puuid)}${playerCol(t2, m.self_puuid)}</div>
    <span class="arrow">▼</span></div><div class="detail" hidden></div>`;
}

const scoreTag = (x) => (x.score == null ? "" : `<span class="sc${x.tag ? " " + x.tag.toLowerCase() : ""}" title="${x.tag ? (x.tag === "MVP" ? "Best performer on the winning team" : "Best performer on the losing team") : "Performance score out of 10"}">${x.tag ? x.tag + " " : ""}${x.score.toFixed(1)}</span>`);
const mvpStrip = (s) => `<div class="mvps">${[...s.members].sort((a, b) => (b.avg_score || 0) - (a.avg_score || 0)).map((m) => `<span class="mv" title="Average score ${m.avg_score}/10 · ${m.mvps} MVP, ${m.aces} ACE in ${s.games} games"><b>${pname(m.player_id)}</b> ${m.avg_score ?? "–"}${m.mvps ? ` <span class="sc mvp">${m.mvps} MVP</span>` : ""}${m.aces ? ` <span class="sc ace">${m.aces} ACE</span>` : ""}</span>`).join("")}</div>`;

function squadRow(m) {
  const own = (id) => (players.find((p) => p.id === id) || {}).name?.toLowerCase();
  return `<div class="mrow sq ${m.win ? "win" : "loss"}" data-mid="${esc(m.match_id)}">
    <div class="mmeta"><span class="res">${m.win ? "Victory" : "Defeat"}</span>
      <span class="mut">${ago(m.game_creation)}${queue() ? "" : ` · ${esc(m.queue)}`}</span>${dchip(m.difficulty)}</div>
    <div class="sqlines">${m.lines.map((l) => `<div class="sqp">${champIco(l.champion)}<div class="sqt">
      <span class="pn">${pname(l.player_id)}${l.account && gname(l.account).toLowerCase() !== own(l.player_id) ? ` · ${esc(gname(l.account))}` : ""}</span>
      <b>${l.kills}/${l.deaths}/${l.assists}</b>${scoreTag(l)}</div></div>`).join("")}</div>
    <span class="arrow">▼</span></div><div class="detail" hidden></div>`;
}

function bindMatches(root) {
  root.querySelectorAll(".mrow[data-mid]:not([data-bound])").forEach((el) => {
    el.dataset.bound = 1;
    el.onclick = async () => {
      const box = el.nextElementSibling;
      if (!box.hidden) { box.hidden = true; return; }
      if (!box.dataset.loaded) {
        try {
          const d = await api(`/matches/${encodeURIComponent(el.dataset.mid)}`);
          const focus = el.closest("#ml") ? (players.find((x) => x.id === selPlayer) || {}).name : null;
          const teams = [...new Set(d.participants.map((p) => p.team_id))];
          const maxDmg = Math.max(1, ...d.participants.map((p) => p.damage));
          box.innerHTML = teams.map((t) => {
            const ps = d.participants.filter((p) => p.team_id === t);
            return teamBlock(ps, d, maxDmg, focus);
          }).join("");
          box.dataset.loaded = 1;
        } catch (e) { box.textContent = e.message; }
      }
      box.hidden = false;
    };
  });
}

/* ---------- players ---------- */
const avatarFor = (s) => (s.champions[0] ? champIco(s.champions[0].champion, "xl") : `<span class="ico xl"></span>`);

function summaryHtml(s) {
  const m = s.summary;
  if (!m.games) return `<div class="summary mut">No matches found.</div>`;
  return `<div class="summary">${donut(m.winrate, 60)}
    <div><div class="sbig ${wcls(m.winrate)}">${m.winrate}% WR</div><div class="mut">${m.games} games · ${m.wins}W ${m.losses}L</div></div>
    <div><div class="sbig">${m.kda} KDA</div><div class="mut">${m.avg_kills} / ${m.avg_deaths} / ${m.avg_assists}</div></div>
    <span class="grow"></span>
    ${m.top_champions.map((c) => `<div class="sc">${champIco(c.champion, "lg")}<div><b class="${wcls(c.winrate)}">${c.winrate}%</b> <span class="mut">(${c.wins}W ${c.losses}L)</span><br><span class="mut">${c.kda} KDA</span></div></div>`).join("")}
  </div>`;
}

function matchupBlock(m) {
  if (!m || (!m.worst.length && !m.best.length && !m.as)) return "";
  const row = (c) => `<div class="list-row">${champIco(c.champion, "sm")}<div class="name">${esc(c.champion)}</div>
    <div class="num"><b class="${wcls(c.winrate)}">${c.winrate}%</b><div class="mut">${c.wins}W ${c.losses}L</div></div></div>`;
  const part = (t, list) => (list.length ? `<div class="mu-t">${t}</div>${list.map(row).join("")}` : "");
  const asSel = `<select id="muas" class="mu-q" title="Played as">${[["", "As any champion"], ...m.played.map((c) => [c.champion, `${c.champion} (${c.games})`])].map(([v, l]) => `<option value="${esc(v)}" ${v === m.as ? "selected" : ""}>${esc(l)}</option>`).join("")}</select>`;
  return tblock("Lane matchups", `${asSel}<input id="muq" class="mu-q" placeholder="Filter any champion…">
    <div id="mu">${part("Struggles against", m.worst)}${part("Does well against", m.best)}
    <div class="mut mu-n">Opponent in the same role, at least ${m.min_games} games.</div></div>`);
}

function bindMatchups(m, onAs) {
  const sel = $("#muas");
  if (sel) sel.onchange = (e) => onAs(e.target.value);
  const box = $("#muq");
  if (!box) return;
  const home = $("#mu").innerHTML;
  const row = (c) => `<div class="list-row">${champIco(c.champion, "sm")}<div class="name">${esc(c.champion)}</div>
    <div class="num"><b class="${wcls(c.winrate)}">${c.winrate}%</b><div class="mut">${c.wins}W ${c.losses}L</div></div></div>`;
  box.oninput = () => {
    const t = box.value.trim().toLowerCase();
    if (!t) { $("#mu").innerHTML = home; return; }
    const hits = m.all.filter((c) => c.champion.toLowerCase().includes(t));
    $("#mu").innerHTML = hits.length ? `<div class="mu-all">${hits.map(row).join("")}</div><div class="mut mu-n">Includes every matchup, even 1 game.</div>` : none("No matchup with that champion");
  };
}

function sidebar(s) {
  const o = s.overall;
  const champRow = (c) => `<div class="list-row">${champIco(c.champion)}
    <div class="name"><b>${esc(c.champion)}</b><div class="mut">${c.cs_per_min} CS/min</div></div>
    <div class="num"><b>${c.kda} KDA</b><div class="mut">${c.avg_kills}/${c.avg_deaths}/${c.avg_assists}</div></div>
    <div class="num"><b class="${wcls(c.winrate)}">${c.winrate}%</b><div class="mut">${c.games} games</div></div></div>`;
  const maxRoles = Math.max(1, ...s.positions.map((p) => p.games));
  return `
    ${tblock("Overview", `<div class="stat-top">${donut(o.winrate, 84, o.games)}
        <div><div class="sbig" style="font-size:20px"><b class="${wcls(o.winrate)}">${o.winrate}%</b> win rate</div>
        <div class="mut">${o.games} games · ${o.wins}W ${o.losses}L</div></div></div>
      <div class="kv"><div><b>${o.kda}</b><span>KDA</span></div><div><b>${o.cs_per_min}</b><span>CS / min</span></div><div><b>${o.vision_per_game}</b><span>Vision</span></div></div>
      ${o.difficulty ? `<div class="diffline"><span class="mut">Opponent strength</span> ${davg(o.difficulty)}</div>` : ""}`)}
    ${tblock("Champions", s.champions.length ? `<div id="champs">${s.champions.slice(0, 7).map(champRow).join("")}</div>${s.champions.length > 7 ? `<button class="linkbtn" id="moreChamps">See all ${s.champions.length} champions</button>` : ""}` : none("No data"))}
    ${tblock("Roles", `<div class="roles">${s.positions.map((p) => `<div class="list-row"><span style="width:60px">${ROLE[p.position] || p.position}</span>
      <div class="bar" style="width:${Math.max(18, 100 * p.games / maxRoles)}px"><div style="width:${p.winrate}%"></div></div>
      <span class="grow"></span><span class="num"><b class="${wcls(p.winrate)}">${p.winrate}%</b> <span class="mut">${p.games}</span></span></div>`).join("") || none("No data")}</div>`)}
    ${s.accounts.length > 1 ? tblock("Accounts", s.accounts.map((a) => `<div class="list-row"><div class="name">${esc(a.account)}</div><div class="num"><b class="${wcls(a.winrate)}">${a.winrate}%</b><div class="mut">${a.games} games</div></div></div>`).join("")) : ""}
    <div id="mublock">${matchupBlock(s.matchups)}</div>
    ${tblock("Played with", s.teammates.length ? s.teammates.map((t) => `<div class="list-row">${champIco(t.champion, "sm")}
      <div class="name ${t.tracked ? "tracked" : ""}">${who(t.tracked, t.name)}</div>
      <div class="num"><b class="${wcls(t.winrate)}">${t.winrate}%</b><div class="mut">${t.wins}W ${t.losses}L</div></div></div>`).join("") : none("Not enough games"))}`;
}

async function renderPlayers() {
  if (!players.length) { view.innerHTML = none("Add players in the Manage tab first."); return; }
  const active = (await api(`/players?${q()}`)).filter((x) => x.games > 0).map((x) => x.id);
  const visible = players.filter((x) => active.includes(x.id));
  if (!visible.length) { view.innerHTML = none("No games in this queue yet."); return; }
  if (!visible.some((p) => p.id === selPlayer)) selPlayer = (visible.find((p) => p.id === me.player_id) || visible[0]).id;
const url = (extra = "") => `/players/${selPlayer}/stats?${q()}&period=${period}&role=${role}&mu_as=${encodeURIComponent(muAs)}${extra}`;
  const s = await api(url());
  const p = players.find((x) => x.id === selPlayer);
  view.innerHTML = `
    <div class="pills">${visible.map((x) => `<button class="pill ${x.id === selPlayer ? "active" : ""}" data-p="${x.id}">${esc(x.name)}${x.id === me.player_id ? " ★" : ""}</button>`).join("")}</div>
    <div class="card profile">${avatarFor(s)}<div><h1>${esc(p.name)}</h1>
      <div class="chips">${p.accounts.map((a) => `<span class="chip">${esc(a.game_name)}#${esc(a.tag_line)}</span>`).join("")}</div></div></div>
    <div class="cols"><div id="side">${sidebar(s)}</div>
      <div><div class="card"><div class="mh-head"><h3 style="margin:0">Match History</h3>
        <select id="period" title="Time range">${PERIODS.map(([v, l]) => `<option value="${v}" ${v === period ? "selected" : ""}>${l}</option>`).join("")}</select>
        <select id="role" title="Role">${ROLES.map(([v, l]) => `<option value="${v}" ${v === role ? "selected" : ""}>${l}</option>`).join("")}</select>
        <input id="search" placeholder="Search champion or played with…"></div>
        <div id="sum">${summaryHtml(s)}</div><div id="ml">${s.recent.map(matchRow).join("")}</div>
        <button class="linkbtn" id="more" ${s.recent.length >= s.total_matches ? "hidden" : ""}>Show more</button></div></div></div>`;

  view.querySelectorAll("[data-p]").forEach((b) => (b.onclick = () => { selPlayer = +b.dataset.p; muAs = ""; render(); }));
  $("#role").onchange = (e) => { role = e.target.value; muAs = ""; render(); };
  $("#period").onchange = (e) => { period = e.target.value; muAs = ""; render(); };
  bindMatchups(s.matchups, async function onAs(v) {
    muAs = v;
    const n = await api(url("&limit=1"));
    $("#mublock").innerHTML = matchupBlock(n.matchups);
    bindMatchups(n.matchups, onAs);
  });
  const mc = $("#moreChamps");
  if (mc) mc.onclick = () => { showAllChamps(s); mc.remove(); };
  bindMatches($("#ml"));

  let shown = s.recent.length, total = s.total_matches, search = "";
  const more = $("#more");
  const updateMore = () => { more.hidden = shown >= total; more.textContent = `Show more (${shown}/${total})`; };
  updateMore();
  more.onclick = async () => {
    const n = await api(url(`&offset=${shown}&limit=20&search=${encodeURIComponent(search)}`));
    shown += n.recent.length;
    $("#ml").insertAdjacentHTML("beforeend", n.recent.map(matchRow).join(""));
    bindMatches($("#ml")); updateMore();
  };
  let timer;
  $("#search").oninput = (e) => {
    clearTimeout(timer);
    timer = setTimeout(async () => {
      search = e.target.value;
      const n = await api(url(`&search=${encodeURIComponent(search)}`));
      shown = n.recent.length; total = n.total_matches;
      $("#sum").innerHTML = summaryHtml(n);
      $("#ml").innerHTML = n.recent.length ? n.recent.map(matchRow).join("") : none("No matches found");
      bindMatches($("#ml")); updateMore();
    }, 300);
  };
}

function showAllChamps(s) {
  $("#champs").innerHTML = s.champions.map((c) => `<div class="list-row">${champIco(c.champion)}
    <div class="name"><b>${esc(c.champion)}</b><div class="mut">${c.cs_per_min} CS/min</div></div>
    <div class="num"><b>${c.kda} KDA</b><div class="mut">${c.avg_kills}/${c.avg_deaths}/${c.avg_assists}</div></div>
    <div class="num"><b class="${wcls(c.winrate)}">${c.winrate}%</b><div class="mut">${c.games} games</div></div></div>`).join("");
}

/* ---------- squads ---------- */
const wlbar = (s) => `<div class="wl"><div class="wlbar"><i style="width:${s.winrate}%"></i></div><span class="${wcls(s.winrate)}">${s.winrate}%</span></div>`;
const ex = { A: [], B: null, active: "A" };
const exData = {};
const PERIODS = [["all", "All time"], ["season", "This season"], ["prev", "Last season"], ["90", "Last 90 days"], ["30", "Last 30 days"], ["7", "Last 7 days"]];
let period = "all", role = "", muAs = "";
const ROLES = [["", "All roles"], ["TOP", "Top"], ["JUNGLE", "Jungle"], ["MIDDLE", "Mid"], ["BOTTOM", "Bot"], ["UTILITY", "Support"]];
let exExact = false;
const xq = () => `${q()}${exExact ? "&exact=true" : ""}`;
let saved = [];

const xDelta = (d) => `<span class="${d >= 0 ? "win" : "loss"}">${d > 0 ? "+" : ""}${d}</span>`;

const round1 = (n) => Math.round(n * 10) / 10;
const sameSet = (a, b) => a.length === b.length && a.every((i) => b.includes(i));
const slots = () => (ex.B ? ["A", "B"] : ["A"]);
const refresh = () => slots().forEach(xFill);

function markActive() {
  document.querySelectorAll(".xpanel").forEach((p) => p.classList.toggle("active", !!ex.B && p.id === `x${ex.active}`));
}

function setSlot(slot, ids) { ex[slot] = ids; xFill(slot); }

async function saveSquad(slot) {
  const ids = ex[slot];
  const name = ids.map((i) => (players.find((p) => p.id === i) || {}).name).join(" + ");
  try { await api("/squads", { method: "POST", body: { name, player_ids: ids } }); toast("Squad saved - use ✎ to rename it"); renderSquads(); }
  catch (err) { toast(err.message, true); }
}

// Header buttons of an explorer panel: one definition drives rendering and click handling.
const XACT = [
  { key: "clear", icon: "↺", title: "Clear selection", show: (s) => ex[s].length > 0, run: (s) => setSlot(s, []) },
  { key: "save", icon: "☆", title: "Save as squad", show: (s) => ex[s].length > 1 && !saved.some((q) => sameSet(q.player_ids, ex[s])), run: saveSquad },
  { key: "close", icon: "✕", title: "Close this panel", show: (s) => s === "B", run: () => { ex.B = null; ex.active = "A"; delete exData.B; renderSquads(); } },
];

function xCompare() {
  const box = $("#xcmp");
  if (!box) return;
  const a = exData.A, b = exData.B;
  if (!a || !b || !ex.A.length || !ex.B.length) { box.innerHTML = ""; return; }
  const side = (s, e) => `<div class="h2hs"><b class="xlabel">${s}</b><span class="pct ${wcls(e.winrate)}">${e.winrate}%</span>
    <span class="mut">${e.games} games</span>${eloChip(e.elo)}${davg(e.difficulty)}</div>`;
  const d = round1(b.winrate - a.winrate);
  const gap = a.difficulty && b.difficulty ? round1(b.difficulty.diff - a.difficulty.diff) : null;
  const opp = gap === null ? "" : Math.abs(gap) < 0.3 ? " · similar opposition"
    : ` · B faced ${gap > 0 ? "tougher" : "easier"} opposition (${dsign(gap)} div)`;
  box.innerHTML = `<div class="h2h">${side("A", a)}<span class="vs">vs</span>${side("B", b)}
    <div class="h2hv mut">B is ${xDelta(d)} pts ${d >= 0 ? "better" : "worse"}${opp}</div></div>`;
}

async function xGames(slot) {
  const box = $(`#g${slot}`);
  const qs = (extra = "") => `/squads/custom?players=${ex[slot].join(",")}&${xq()}${extra}`;
  const s = await api(qs("&limit=10"));
  box.innerHTML = `${s.games ? mvpStrip(s) : ""}<div class="sml">${s.recent.map(squadRow).join("") || none("No games found")}</div><button class="linkbtn" hidden></button>`;
  const list = box.querySelector(".sml"), btn = box.querySelector("button");
  let shown = s.recent.length;
  const upd = () => { btn.hidden = shown >= s.total_matches; btn.textContent = `Show more (${shown}/${s.total_matches})`; };
  bindMatches(list); upd();
  btn.onclick = async () => {
    const n = await api(qs(`&offset=${shown}&limit=20`));
    shown += n.recent.length;
    list.insertAdjacentHTML("beforeend", n.recent.map(squadRow).join(""));
    bindMatches(list); upd();
  };
}

async function xFill(slot) {
  const box = $(`#x${slot}`);
  if (!box) return;
  const ids = ex[slot];
  const e = await api(`/squads/explore?players=${ids.join(",")}&${xq()}`);
  exData[slot] = e;
  const adds = e.additions;
  const chips = ids.map((id) => `<span class="chip acc">${pname(id)}<a data-rm="${id}" title="Remove">×</a></span>`).join("");
  const addHtml = adds.length
    ? adds.map((a) => `<button class="xcell" data-add="${(a.player_ids || [a.player_id]).join(",")}"><span class="nm">${ids.length ? "+ " : ""}${(a.player_ids || [a.player_id]).map(pname).join(" + ")}</span>
        <span class="mut">${a.games}g</span><b class="${wcls(a.winrate)}">${a.winrate}%</b>${ids.length ? xDelta(a.delta) : "<span></span>"}</button>`).join("")
    : `<div class="xempty">${ids.length ? "No one else has played with this group." : "No data for this queue yet."}</div>`;
  box.innerHTML = `<div class="xhead"><b class="xlabel">${slot}</b>
      <div class="chips xpath">${chips || '<span class="mut">Pick a starting player</span>'}</div>
      <div class="xacts">${XACT.filter((a) => a.show(slot)).map((a) => `<button class="ibtn" data-act="${a.key}" title="${a.title}">${a.icon}</button>`).join("")}</div></div>
    ${ids.length ? `<div class="xsum"><span class="pct ${wcls(e.winrate)}">${e.winrate}%</span>
      <div class="wlbar"><i style="width:${e.winrate}%"></i></div>
      <span class="mut">${e.games} games · <span class="win">${e.wins}W</span> <span class="loss">${e.losses}L</span></span>${eloChip(e.elo)}${davg(e.difficulty)}</div>${flexNote(ids.length) ? `<div class="xempty">${flexNote(ids.length)}</div>` : ""}${exExact && ids.length >= 5 ? `<div class="xempty">A full 5-player squad has no one else on the team, so Only together changes nothing here.</div>` : ""}` : ""}
    ${ids.length >= maxSquad() ? "" : `<div class="sect">${ids.length ? "Add a player" : "Start from"}</div><div class="xadd">${addHtml}</div>`}
    ${ids.length ? `<div class="sect">Games together</div><div id="g${slot}"></div>` : ""}`;
  box.querySelectorAll("[data-add]").forEach((el) => (el.onclick = () => setSlot(slot, [...ids, ...el.dataset.add.split(",").map(Number)])));
  box.querySelectorAll("[data-rm]").forEach((el) => (el.onclick = () => setSlot(slot, ids.filter((i) => i !== +el.dataset.rm))));
  box.querySelectorAll("[data-act]").forEach((el) => (el.onclick = () => XACT.find((a) => a.key === el.dataset.act).run(slot)));
  box.onclick = () => { ex.active = slot; markActive(); };
  markActive();
  if (ids.length) xGames(slot);
  xCompare();
}

async function renderSquads() {
  saved = await api(`/squads?${xq()}`);
  view.innerHTML = `<div class="xbar">
      <label class="tog" title="Only count games where no other tracked player was on the team"><input type="checkbox" id="xexact" ${exExact ? "checked" : ""}> Only together</label>
      <span class="grow"></span>
      <button id="xtoggle" class="${ex.B ? "" : "primary"}">${ex.B ? "Stop comparing" : "Compare"}</button></div>
    ${ex.B ? '<div id="xcmp"></div>' : ""}
    ${saved.length ? `<div class="xsaved"><span class="mut">Saved</span>${saved.map((s) => `<span class="schip" data-saved="${s.id}" title="${esc(s.player_ids.map((i) => pname(i)).join(", "))} · ${s.games} games ${s.wins}W ${s.losses}L${ex.B ? ` (loads into ${ex.active})` : ""}">
      <span class="nm">${esc(s.name)}</span><b class="${s.games ? wcls(s.winrate) : "mut"}">${s.games ? s.winrate + "%" : "–"}</b><a data-edit="${s.id}" title="Rename squad">✎</a><a data-del="${s.id}" title="Delete squad">✕</a></span>`).join("")}</div>` : ""}
    <div class="xwrap ${ex.B ? "two" : ""}"><div class="card xpanel" id="xA"></div>${ex.B ? '<div class="card xpanel" id="xB"></div>' : ""}</div>`;
  $("#xexact").onchange = (e) => { exExact = e.target.checked; renderSquads(); };
  $("#xtoggle").onclick = () => { ex.B = ex.B ? null : [...ex.A]; ex.active = "A"; if (!ex.B) delete exData.B; renderSquads(); };
  view.querySelectorAll("[data-saved]").forEach((el) => (el.onclick = () => {
    const s = saved.find((x) => x.id === +el.dataset.saved);
    setSlot(ex.B ? ex.active : "A", [...s.player_ids]);
  }));
  view.querySelectorAll("[data-edit]").forEach((a) => (a.onclick = (e) => {
    e.stopPropagation();
    const s = saved.find((x) => x.id === +a.dataset.edit);
    const chip = a.closest(".schip"), nm = chip.querySelector(".nm");
    const input = document.createElement("input");
    input.className = "schip-in"; input.value = s.name; input.maxLength = 60;
    nm.replaceWith(input); input.focus(); input.select();
    input.onclick = (ev) => ev.stopPropagation();
    let done = false;
    const finish = async (commit) => {
      if (done) return; done = true;
      const name = input.value.trim();
      if (commit && name && name !== s.name) {
        try { await api(`/squads/${s.id}`, { method: "PATCH", body: { name } }); } catch (err) { toast(err.message, true); }
      }
      renderSquads();
    };
    input.onkeydown = (ev) => { if (ev.key === "Enter") finish(true); else if (ev.key === "Escape") finish(false); };
    input.onblur = () => finish(true);
  }));
  view.querySelectorAll("[data-del]").forEach((a) => (a.onclick = async (e) => {
    e.stopPropagation();
    if (a.dataset.armed) { await api(`/squads/${a.dataset.del}`, { method: "DELETE" }); renderSquads(); return; }
    a.dataset.armed = "1"; a.textContent = "Delete?"; a.classList.add("armed");
    setTimeout(() => { a.textContent = "✕"; delete a.dataset.armed; a.classList.remove("armed"); }, 3000);
  }));
  refresh();
}


/* ---------- manage ---------- */
function toast(msg, bad = false) {
  const t = document.createElement("div");
  t.className = "toast" + (bad ? " bad" : "");
  t.textContent = msg;
  document.body.appendChild(t);
  setTimeout(() => t.remove(), 3500);
}

async function renderManage() {
  const [sugg, squadSugg] = await Promise.all([
    api("/suggestions?min_games=3").catch(() => []),
    api(`/squads/suggestions?${q()}`).catch(() => []),
  ]);
  const drafts = [...view.querySelectorAll("input")].map((input) => ({
    id: input.id,
    account: input.dataset.accIn,
    value: input.value,
    focused: input === document.activeElement,
    start: input.selectionStart,
    end: input.selectionEnd,
  }));
  const initial = (n) => esc((n || "?")[0].toUpperCase());
  view.innerHTML = `
    ${squadSugg.length ? `<div class="sect">Suggested squads <span style="text-transform:none">(people who often play on the same team${queue() ? "" : ", all queues"})</span></div>
    <div class="slist" id="squadSugg">${squadSugg.map((s, i) => `<div class="srow static" data-i="${i}">
                <div class="sname">${s.names.map(esc).join(" + ")}</div>
                <div class="sgames">${s.games} games <span class="win">${s.wins}W</span> <span class="loss">${s.losses}L</span></div>
                ${wlbar(s)}<span class="sx"><button class="primary sm" data-make>Create</button></span></div>`).join("")}</div>` : ""}
    <div class="sect">Suggested from your match history <span style="text-transform:none">(untracked players you often play with)</span></div>
    <div class="grid" id="sugg">${sugg.length ? sugg.map((s) => `<div class="scard sugg" data-puuid="${esc(s.puuid)}">
        ${champIco(s.champion, "lg")}
        <div class="grow"><h4>${esc(s.game_name || s.riot_id)}<span class="mut">${s.tag_line ? "#" + esc(s.tag_line) : ""}</span></h4>
          <div class="mem">${s.games} games together · <b class="${wcls(s.winrate)}">${s.winrate}%</b> WR · ${ago(s.last_played)}</div>
          <div class="mem">with ${s.with.map(esc).join(", ")}</div>
          <div class="row" style="margin:8px 0 0"><button class="primary" data-add-new>Add as person</button>
            <select data-add-to><option value="">Add to existing…</option>${players.map((p) => `<option value="${p.id}">${esc(p.name)}</option>`).join("")}</select></div></div>
        <a data-dismiss title="Not interested">✕</a></div>`).join("") : none("No frequent teammates found yet. Sync more matches.")}</div>

    <div class="sect">People</div>
    <div class="card"><div class="row" style="margin:0"><input id="pn" placeholder="Name of a friend (a real person)" style="flex:1;min-width:200px">
      <input id="pa" placeholder="Riot ID, e.g. Name#EUW (optional)" style="flex:1;min-width:200px"><button id="addP" class="primary">Add person</button></div></div>
    <div class="grid people">${players.map((p) => `<div class="card person"><div class="phead"><span class="avatar">${initial(p.name)}</span>
        <h4 class="grow">${esc(p.name)}</h4><a data-del-player="${p.id}" title="Remove person">✕</a></div>
      <div class="chips">${p.accounts.map((a) => `<span class="chip acc">${esc(a.game_name)}<span class="mut">#${esc(a.tag_line)}</span>
        <a data-del-acc="${esc(a.puuid)}" title="Remove account">×</a></span>`).join("") || '<span class="mut">No accounts yet</span>'}</div>
      <div class="row" style="margin:10px 0 0"><input data-acc-in="${p.id}" placeholder="Add account: Name#TAG" style="flex:1;min-width:0"><button data-add-acc="${p.id}">Add</button></div></div>`).join("")}</div>

    <div class="sect">Create a squad</div>
    <div class="card"><div class="row"><input id="sn" placeholder="Squad name" style="flex:1;min-width:180px"></div>
      <div class="chips" id="sqpick">${players.map((p) => `<button class="pickchip" data-pid="${p.id}">${esc(p.name)}</button>`).join("") || '<span class="mut">Add people first</span>'}</div>
      <div class="row" style="margin:10px 0 0"><button id="addS" class="primary">Create squad</button><span class="mut">Pick 2 or more people</span></div></div>`;

  drafts.forEach((draft) => {
    const input = draft.id ? document.getElementById(draft.id) : view.querySelector(`[data-acc-in="${draft.account}"]`);
    if (!input) return;
    input.value = draft.value;
    if (draft.focused) {
      input.focus();
      input.setSelectionRange(draft.start, draft.end);
    }
  });

  const run = (fn, ok) => async (e) => {
    try { const r = await fn(e); await loadPlayers(); if (ok) toast(typeof ok === "function" ? ok(r) : ok); render(); }
    catch (err) { toast(err.message, true); }
  };
  const syncNote = (r) => (r && r.sync_started ? "Added. Downloading their matches…" : "Added. Press Sync when the current sync finishes.");
  const kick = () => pollSync();

  view.querySelectorAll("#squadSugg .srow").forEach((card) => {
    const s = squadSugg[+card.dataset.i];
    card.querySelector("[data-make]").onclick = run(async () => {
      const base = s.names.join(" + ");
      for (let n = 1; ; n++) {
        try { return await api("/squads", { method: "POST", body: { name: n === 1 ? base : `${base} ${n}`, player_ids: s.player_ids } }); }
        catch (e) { if (!/already exists/.test(e.message)) throw e; }
      }
    }, "Squad created");
  });

  view.querySelectorAll(".sugg").forEach((card) => {
    const puuid = card.dataset.puuid;
    card.querySelector("[data-add-new]").onclick = run(async () => { const r = await api("/suggestions/add", { method: "POST", body: { puuid } }); kick(); return r; }, syncNote);
    card.querySelector("[data-add-to]").onchange = run(async (e) => {
      if (!e.target.value) return;
      const r = await api("/suggestions/add", { method: "POST", body: { puuid, player_id: +e.target.value } }); kick(); return r;
    }, syncNote);
    card.querySelector("[data-dismiss]").onclick = run(() => api("/suggestions/dismiss", { method: "POST", body: { puuid } }));
  });

  const addPerson = run(async () => {
    const name = $("#pn").value.trim();
    if (!name) throw new Error("Enter a name");
    const p = await api("/players", { method: "POST", body: { name } });
    const rid = $("#pa").value.trim();
    if (rid) await api(`/players/${p.id}/accounts`, { method: "POST", body: { riot_id: rid } });
  }, "Person added");
  $("#addP").onclick = addPerson;
  [$("#pn"), $("#pa")].forEach((i) => (i.onkeydown = (e) => { if (e.key === "Enter") addPerson(); }));

  view.querySelectorAll("[data-add-acc]").forEach((b) => {
    const input = view.querySelector(`[data-acc-in="${b.dataset.addAcc}"]`);
    const go = run(() => api(`/players/${b.dataset.addAcc}/accounts`, { method: "POST", body: { riot_id: input.value } }), "Account added. Press Sync to download its matches.");
    b.onclick = go;
    input.onkeydown = (e) => { if (e.key === "Enter") go(); };
  });
  view.querySelectorAll("[data-del-acc]").forEach((a) => (a.onclick = run(() => confirm("Remove this account?") && api(`/accounts/${encodeURIComponent(a.dataset.delAcc)}`, { method: "DELETE" }))));
  view.querySelectorAll("[data-del-player]").forEach((a) => (a.onclick = run(() => confirm("Remove this person and their accounts?") && api(`/players/${a.dataset.delPlayer}`, { method: "DELETE" }))));

  view.querySelectorAll(".pickchip").forEach((c) => (c.onclick = () => c.classList.toggle("on")));
  $("#addS").onclick = run(() => api("/squads", { method: "POST", body: { name: $("#sn").value, player_ids: [...view.querySelectorAll(".pickchip.on")].map((c) => +c.dataset.pid) } }), "Squad created");
}

/* ---------- shell ---------- */
const loadPlayers = async () => (players = await api("/players"));
const render = () => Promise.resolve(({ squads: renderSquads, players: renderPlayers, manage: renderManage, account: renderAccount })[tab]())
  .catch((e) => { console.error(e); view.textContent = e.message; });

async function pollSync() {
  const s = await api("/sync/status");
  const bar = $("#syncStatus");
  const r = s.ranks && s.ranks.running ? s.ranks.message : "";
  const text = s.running ? s.message : s.errors.length ? `${s.message} (${s.errors.length} errors: ${s.errors[0]})` : r;
  bar.hidden = !text; bar.textContent = text;
  $("#syncBtn").disabled = s.running;
  if (s.running || r) setTimeout(pollSync, 2000); else if (pollSync.was) { await loadPlayers(); render(); }
  pollSync.was = s.running || !!r;
}

document.querySelectorAll("nav button").forEach((b) => (b.onclick = () => {
  document.querySelectorAll("nav button").forEach((x) => x.classList.remove("active"));
  b.classList.add("active"); tab = b.dataset.tab; render();
}));
$("#queue").onchange = render;
$("#syncBtn").onclick = async () => { try { await api("/sync", { method: "POST" }); pollSync(); } catch (e) { alert(e.message); } };

/* ---------- accounts ---------- */
function applyUser() {
  document.body.classList.remove("loggedout");
  document.body.classList.toggle("member", me.role !== "admin");
  if (me.role !== "admin" && tab === "manage") { tab = "squads"; }
  document.querySelectorAll("nav button").forEach((x) => x.classList.toggle("active", x.dataset.tab === tab));
}

async function showLogin() {
  if (loginShown) return;
  loginShown = true;
  document.body.classList.add("loggedout");
  const { has_users } = await fetch("/api/auth/status").then((r) => r.json());
  let mode = has_users ? "login" : "register";
  const draw = () => {
    const reg = mode === "register";
    view.innerHTML = `<form class="card authbox" autocomplete="on">
      <h2>⚔ Pre<span>mo</span></h2>
      <div class="authtabs"><button type="button" data-m="login" class="${reg ? "" : "on"}">Sign in</button><button type="button" data-m="register" class="${reg ? "on" : ""}">Create account</button></div>
      <input id="au" placeholder="Username" autocomplete="username" required>
      <input id="ap" type="password" placeholder="Password${reg ? " (8+ characters)" : ""}" autocomplete="${reg ? "new-password" : "current-password"}" required>
      ${reg ? `<input id="ai" placeholder="${has_users ? "Invite code" : "Setup code (data/setup_code.txt on the server)"}" autocomplete="off" required>` : ""}
      <div class="autherr" id="ae" hidden></div>
      <button class="primary" type="submit">${reg ? "Create account" : "Sign in"}</button></form>`;
    view.querySelectorAll("[data-m]").forEach((b) => (b.onclick = () => { mode = b.dataset.m; draw(); }));
    $("#au").focus();
    view.querySelector("form").onsubmit = async (e) => {
      e.preventDefault();
      const body = { username: $("#au").value, password: $("#ap").value };
      if (reg) body.invite = $("#ai").value;
      try {
        me = await api(reg ? "/auth/register" : "/auth/login", { method: "POST", body });
        loginShown = false;
        await boot();
      } catch (err) { const box = $("#ae"); box.hidden = false; box.textContent = err.message; }
    };
  };
  draw();
}

async function renderAccount() {
  const adm = me.role === "admin";
  const [users, invites] = adm ? await Promise.all([api("/auth/users"), api("/auth/invites")]) : [[], []];
  const when = (t) => new Date(t * 1000).toLocaleDateString();
  view.innerHTML = `<div class="acct">
    <div class="card"><h3 style="margin-top:0">${esc(me.username)} <span class="chip">${adm ? "Admin" : "Member"}</span></h3>
      <label class="mut">I am</label>
      <select id="mePlayer"><option value="">Nobody in particular</option>${players.map((p) => `<option value="${p.id}" ${p.id === me.player_id ? "selected" : ""}>${esc(p.name)}</option>`).join("")}</select>
      <div class="mut" style="margin:4px 0 14px">Your Players tab opens on this person.</div>
      <div class="sect">Change password</div>
      <div class="arow"><input id="cp" type="password" placeholder="Current password" autocomplete="current-password">
        <input id="np" type="password" placeholder="New password (8+)" autocomplete="new-password"><button id="chpw">Change</button></div>
      <div class="arow"><button id="logout">Sign out</button></div></div>
    ${adm ? `<div class="card"><div class="mh-head"><h3 style="margin:0">Invite codes</h3><button id="newInv" class="primary">New invite</button></div>
      <div class="mut" style="margin:6px 0">Each code works once. Send it to a friend so they can create an account.</div>
      ${invites.length ? invites.map((i) => `<div class="urow"><code>${esc(i.code)}</code>
        <span class="mut grow">${i.used_at ? `used${i.used_by ? " by " + esc(i.used_by) : ""}` : `created ${when(i.created_at)}`}</span>
        ${i.used_at ? "" : `<button class="ibtn" data-copy="${esc(i.code)}" title="Copy">⧉</button>`}<a class="x" data-delinv="${esc(i.code)}" title="Delete">✕</a></div>`).join("") : '<div class="mut">No codes yet.</div>'}</div>
    <div class="card"><h3 style="margin-top:0">Accounts</h3>
      ${users.map((u) => `<div class="urow"><b>${esc(u.username)}</b><span class="chip">${u.role}</span>
        <span class="mut grow">${u.player_id ? "is " + pname(u.player_id) : ""}</span>
        ${u.id === me.id ? '<span class="mut">you</span>' : `<button class="ibtn" data-role="${u.id}" data-to="${u.role === "admin" ? "member" : "admin"}">${u.role === "admin" ? "Make member" : "Make admin"}</button>
        <a class="x" data-deluser="${u.id}" title="Delete account">✕</a>`}</div>`).join("")}</div>` : ""}</div>`;
  const go = (fn, ok) => async (e) => { try { await fn(e); if (ok) toast(ok); render(); } catch (err) { toast(err.message, true); } };
  $("#mePlayer").onchange = go(async (e) => {
    me = await api("/auth/me", { method: "PATCH", body: e.target.value ? { player_id: +e.target.value } : { clear_player: true } });
  }, "Saved");
  $("#chpw").onclick = go(async () => {
    await api("/auth/me", { method: "PATCH", body: { current_password: $("#cp").value, new_password: $("#np").value } });
  }, "Password changed");
  $("#logout").onclick = async () => { await api("/auth/logout", { method: "POST" }); location.reload(); };
  if (!adm) return;
  $("#newInv").onclick = go(() => api("/auth/invites", { method: "POST" }));
  view.querySelectorAll("[data-copy]").forEach((b) => (b.onclick = () => navigator.clipboard.writeText(b.dataset.copy).then(() => toast("Copied"))));
  view.querySelectorAll("[data-delinv]").forEach((a) => (a.onclick = go(() => api(`/auth/invites/${encodeURIComponent(a.dataset.delinv)}`, { method: "DELETE" }))));
  view.querySelectorAll("[data-role]").forEach((b) => (b.onclick = go(() => api(`/auth/users/${b.dataset.role}`, { method: "PATCH", body: { role: b.dataset.to } }))));
  view.querySelectorAll("[data-deluser]").forEach((a) => (a.onclick = go(async () => {
    if (!a.dataset.armed) { a.dataset.armed = "1"; a.textContent = "Delete?"; a.classList.add("armed"); throw new Error("Click again to delete this account"); }
    await api(`/auth/users/${a.dataset.deluser}`, { method: "DELETE" });
  })));
}

async function boot() {
  applyUser();
  const cfg = await api("/config");
  $("#queue").innerHTML = `<option value="">All queues</option>` + Object.entries(cfg.queues).sort((a, b) => a[0] - b[0]).map(([id, n]) => `<option value="${id}" ${id === "440" ? "selected" : ""}>${n}</option>`).join("");
  if (!cfg.has_key && me.role === "admin") alert("RIOT_API_KEY is not set. Copy .env.example to .env and fill it in.");
  await Promise.all([loadPlayers(), loadDDragon()]); render(); pollSync();
}

(async () => {
  try { me = await api("/auth/me"); } catch { return showLogin(); }
  boot();
})();