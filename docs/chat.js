const MONTH_NAMES = [
  ["january", "jan"],
  ["february", "feb"],
  ["march", "mar"],
  ["april", "apr"],
  ["may", "may"],
  ["june", "jun"],
  ["july", "jul"],
  ["august", "aug"],
  ["september", "sep"],
  ["october", "oct"],
  ["november", "nov"],
  ["december", "dec"],
];

let greeted = false;

function fold(text) {
  return text
    .toLowerCase()
    .normalize("NFD")
    .replace(/[\u0300-\u036f]/g, "")
    .replace(/[^a-z0-9\s]/g, " ")
    .replace(/\s+/g, " ")
    .trim();
}

function escapeText(value) {
  return String(value).replace(/[&<>"]/g, (ch) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[ch]));
}

function flow(value) {
  return `${Math.round(value).toLocaleString("en-GB")} m³/s`;
}

function stamp(iso) {
  const [year, month, day] = iso.split("-").map(Number);
  const names = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
  return `${day} ${names[month - 1]} ${year}`;
}

function data() {
  return window.WaterPulse ? window.WaterPulse.data() : null;
}

function extreme(days, key, high) {
  let best = 0;
  days.forEach((day, index) => {
    if (high ? day[key] > days[best][key] : day[key] < days[best][key]) best = index;
  });
  return best;
}

function findDates(question, days) {
  const found = [];
  const iso = question.match(/\b(2025-\d{2}-\d{2})\b/g) || [];
  iso.forEach((token) => {
    const index = days.findIndex((day) => day.t === token);
    if (index >= 0) found.push(index);
  });
  const text = ` ${fold(question)} `;
  MONTH_NAMES.forEach((names, month) => {
    names.forEach((name) => {
      const before = new RegExp(`\\b(\\d{1,2})(?:st|nd|rd|th)?\\s+${name}\\b`, "g");
      const after = new RegExp(`\\b${name}\\s+(\\d{1,2})(?:st|nd|rd|th)?\\b`, "g");
      [before, after].forEach((pattern) => {
        let match = pattern.exec(text);
        while (match) {
          const day = Number(match[1]);
          const isoDay = `2025-${String(month + 1).padStart(2, "0")}-${String(day).padStart(2, "0")}`;
          const index = days.findIndex((row) => row.t === isoDay);
          if (index >= 0 && !found.includes(index)) found.push(index);
          match = pattern.exec(text);
        }
      });
    });
  });
  return found;
}

function findOutlets(question, outlets) {
  const text = ` ${fold(question)} `;
  return outlets.filter((outlet) => {
    const name = fold(outlet.name);
    return text.includes(` ${name} `) || text.includes(` ${outlet.id} `);
  });
}

function answer(question) {
  const year = data();
  if (!year) return { html: "The daily series is still loading." };
  const q = fold(question);
  const days = year.days;
  const dates = findDates(question, days);
  const outlets = findOutlets(question, year.outlets);
  const here = dates[0] != null ? dates[0] : window.WaterPulse.current();
  const high = /\b(peak|highest|maximum|most|wettest|biggest flood|largest)\b/.test(q);
  const low = /\b(lowest|minimum|least|driest|smallest)\b/.test(q);

  if (/\b(what is this|how does this|how do i|what does this|help|how to use)\b/.test(q)) {
    return {
      html: "This is the 2025 film. The slider and the calendar jump to a day. Ask for the peak, a date, or one of the five outlets, and I can open that day.",
    };
  }
  if (/\b(colou?r|blue|brown|cream|anomaly|normal)\b/.test(q)) {
    return {
      html: "Colour is the river against its own 2025 median. Brown is about a tenth of usual, blue is usual, deep blue is about ten times usual. A day above normal also brightens. Ribbon width follows discharge, on one scale for every river.",
    };
  }
  if (/\b(ribbon|thickness|height|width|sqrt|square root)\b/.test(q)) {
    return {
      html: "Ribbon width follows that day’s discharge. The Rhine and a small alpine stream use the same scale, so a river with twice the water is twice as wide.",
    };
  }
  if (/\b(outlet|leaving|the number|headline|sum|balance)\b/.test(q) && !outlets.length && !dates.length && !high && !low) {
    return {
      html: "The number is water leaving Switzerland: the Rhine at Basel, the Rhône at Chancy, the Ticino toward Lago Maggiore, the Inn at Martina, and the Doubs at Ocourt. It is not a closed water balance.",
    };
  }
  if (/\b(source|glofas|copernicus|licence|license|data|hydro)\b/.test(q)) {
    return {
      html: "Daily discharge is GloFAS v4 historical from the Copernicus Emergency Management Service. Rivers are HydroRIVERS, lakes are swissTLMRegio, glaciers are the 2016 Swiss Glacier Inventory, and the hillshade is Mapzen terrain. The <a href=\"about.html\">about page</a> has the limits.",
    };
  }
  if (dates.length >= 2) {
    const left = days[dates[0]];
    const right = days[dates[1]];
    const delta = right.total - left.total;
    const word = delta === 0 ? "the same" : delta > 0 ? `${flow(delta)} higher` : `${flow(-delta)} lower`;
    return {
      html: `${stamp(right.t)} was ${word} than ${stamp(left.t)} (${flow(right.total)} against ${flow(left.total)}).`,
      day: dates[1],
    };
  }
  if ((high || low) && outlets.length) {
    const outlet = outlets[0];
    const index = extreme(days, outlet.id, high);
    const day = days[index];
    return {
      html: `The ${high ? "highest" : "lowest"} ${outlet.name} at ${outlet.place} in 2025 was ${flow(day[outlet.id])} on ${stamp(day.t)}.`,
      day: index,
    };
  }
  if (high || low) {
    const index = extreme(days, "total", high);
    const day = days[index];
    return {
      html: `The ${high ? "peak" : "lowest"} day for water leaving Switzerland was ${stamp(day.t)}, at ${flow(day.total)}.`,
      day: index,
    };
  }
  if (outlets.length || dates.length) {
    const day = days[here];
    if (outlets.length === 1) {
      const outlet = outlets[0];
      return {
        html: `On ${stamp(day.t)} the ${outlet.name} at ${outlet.place} was ${flow(day[outlet.id])}. Water leaving the country that day was ${flow(day.total)}.`,
        day: here,
      };
    }
    const ranked = year.outlets.slice().sort((a, b) => day[b.id] - day[a.id]);
    const lead = ranked[0];
    return {
      html: `On ${stamp(day.t)}, ${flow(day.total)} left Switzerland. The largest share was the ${lead.name} at ${lead.place}, ${flow(day[lead.id])}.`,
      day: here,
    };
  }
  if (q.length < 2) return { html: "Ask for a day, a river, or the peak." };
  return {
    html: "I can answer from the 2025 series: a date, the Rhine, Rhône, Ticino, Inn or Doubs, the peak day, or what the colours mean.",
  };
}

function appendMessage(kind, html) {
  const log = document.getElementById("chat-log");
  const item = document.createElement("div");
  item.className = `chat-msg ${kind}`;
  item.innerHTML = html;
  log.appendChild(item);
  item.querySelectorAll("button[data-day]").forEach((button) => {
    button.addEventListener("click", () => window.WaterPulse.setDay(Number(button.dataset.day), true));
  });
  item.querySelectorAll("button[data-ask]").forEach((button) => {
    button.addEventListener("click", () => submit(button.dataset.ask));
  });
  log.scrollTop = log.scrollHeight;
}

function submit(text) {
  const question = text.trim();
  if (!question) return;
  appendMessage("user", escapeText(question));
  const result = answer(question);
  const open = result.day != null
    ? ` <button type="button" data-day="${result.day}">Show this day</button>`
    : "";
  appendMessage("bot", `<span>${result.html}</span>${open}`);
}

function setChat(open) {
  const chat = document.getElementById("chat");
  const toggle = document.getElementById("btn-chat");
  chat.hidden = !open;
  toggle.classList.toggle("on", open);
  toggle.setAttribute("aria-expanded", open ? "true" : "false");
  if (open && !greeted) {
    greeted = true;
    appendMessage(
      "bot",
      `Ask for a day, a river, or the colour. I answer from the 2025 series and can open the day.
      <div class="chips">
        <button type="button" data-ask="When was the peak?">Peak day</button>
        <button type="button" data-ask="Rhine on 15 June">Rhine on 15 June</button>
        <button type="button" data-ask="What does blue mean?">What blue means</button>
      </div>`
    );
  }
  if (open) document.getElementById("ask-input").focus();
}

document.getElementById("btn-chat").addEventListener("click", () => {
  setChat(document.getElementById("chat").hidden);
});
document.getElementById("chat-close").addEventListener("click", () => setChat(false));
document.getElementById("ask-form").addEventListener("submit", (event) => {
  event.preventDefault();
  const input = document.getElementById("ask-input");
  submit(input.value);
  input.value = "";
});
