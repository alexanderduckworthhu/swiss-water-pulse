const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];

const state = {
  year: null,
  index: 0,
  seeking: false,
};

const film = document.getElementById("film");
const slider = document.getElementById("day");
const calendar = document.getElementById("calendar");
const play = document.getElementById("play");

function stamp(iso) {
  const [year, month, day] = iso.split("-").map(Number);
  return `${day} ${MONTHS[month - 1]} ${year}`;
}

function flow(value) {
  return `${Math.round(value).toLocaleString("en-GB")} m³/s`;
}

function daySeconds(index) {
  const step = state.year.framesPerDay / state.year.fps;
  return index * step;
}

function indexFromTime(time) {
  const step = state.year.framesPerDay / state.year.fps;
  const index = Math.floor(time / step + 1e-4);
  return Math.max(0, Math.min(state.year.days.length - 1, index));
}

function paint(index) {
  const day = state.year.days[index];
  document.getElementById("stamp").textContent = stamp(day.t);
  document.getElementById("total").textContent = flow(day.total);
  document.getElementById("parts").textContent = state.year.outlets
    .map((outlet) => `${outlet.name} ${flow(day[outlet.id])}`)
    .join(" · ");
  slider.value = String(index);
  calendar.value = day.t;
  state.index = index;
}

function setDay(index, seek) {
  if (!state.year) return;
  const next = Math.max(0, Math.min(state.year.days.length - 1, index));
  paint(next);
  if (!seek || !film) return;
  const target = daySeconds(next);
  if (Math.abs(film.currentTime - target) > 0.03) {
    state.seeking = true;
    film.currentTime = target;
  }
}

window.WaterPulse = {
  setDay,
  current() {
    return state.index;
  },
  data() {
    return state.year;
  },
};

slider.addEventListener("input", () => setDay(Number(slider.value), true));
calendar.addEventListener("change", () => {
  const index = state.year.days.findIndex((day) => day.t === calendar.value);
  if (index >= 0) setDay(index, true);
});

play.addEventListener("click", () => {
  if (film.paused) {
    if (film.currentTime >= film.duration - 0.05) film.currentTime = 0;
    film.play();
  } else {
    film.pause();
  }
});

film.addEventListener("play", () => {
  play.textContent = "Pause";
});
film.addEventListener("pause", () => {
  play.textContent = "Play";
});
film.addEventListener("seeked", () => {
  state.seeking = false;
});
film.addEventListener("timeupdate", () => {
  if (state.seeking) return;
  const index = indexFromTime(film.currentTime);
  if (index !== state.index) paint(index);
});

document.addEventListener("keydown", (event) => {
  if (event.target.matches("input, textarea")) return;
  if (event.key === "ArrowRight") setDay(state.index + 1, true);
  if (event.key === "ArrowLeft") setDay(state.index - 1, true);
  if (event.key === " ") {
    event.preventDefault();
    play.click();
  }
});

fetch("year.json")
  .then((response) => {
    if (!response.ok) throw new Error("year.json");
    return response.json();
  })
  .then((year) => {
    state.year = year;
    film.src = "film.mp4?v=7";
    slider.max = String(year.days.length - 1);
    calendar.min = year.days[0].t;
    calendar.max = year.days[year.days.length - 1].t;
    setDay(0, false);
    document.dispatchEvent(new Event("pulse-ready"));
  })
  .catch(() => {
    document.getElementById("parts").textContent = "The daily series did not load.";
  });
