// static/script.js
let currentJob = null;
let pollHandle = null;

function q(selector){ return document.querySelector(selector); }

async function fetchInfo() {
  const url = q("#url").value.trim();
  if (!url) { alert("Enter a URL"); return; }

  q("#videoInfo").classList.add("hidden");
  q(".progress-container").classList.add("hidden");
  q("#progress-bar").style.width = "0%";
  q("#progress-text").innerText = "0%";

  try {
    const res = await fetch("/api/info", {
      method: "POST",
      headers: {"Content-Type": "application/json"},
      body: JSON.stringify({url})
    });
    const data = await res.json();
    if (data.error) throw new Error(data.error || "Failed to fetch");

    q("#thumbnail").src = data.thumbnail || "";
    q("#title").innerText = data.title || "";
    const sel = q("#quality");
    sel.innerHTML = "";

    // Populate formats (label: resolution / ext / size)
    data.formats.forEach(f => {
      const labelParts = [];
      if (f.height) labelParts.push(`${f.height}p`);
      else if (f.format_note) labelParts.push(f.format_note);
      labelParts.push(f.ext);
      if (f.filesize) labelParts.push(`~${Math.round(f.filesize/1024/1024)}MB`);
      const opt = document.createElement("option");
      opt.value = f.format_id;
      opt.innerText = labelParts.join(" • ");
      sel.appendChild(opt);
    });

    q("#videoInfo").classList.remove("hidden");
  } catch (e) {
    alert("Error fetching info: " + (e.message || e));
  }
}

async function startDownload(){
  const url = q("#url").value.trim();
  if (!url) return alert("Enter a URL");
  const format_id = q("#quality").value || "";

  try {
    const res = await fetch("/api/download", {
      method: "POST",
      headers: {"Content-Type": "application/json"},
      body: JSON.stringify({url, format_id})
    });
    const data = await res.json();
    if (!data.job_id) throw new Error("Failed to start job");

    currentJob = data.job_id;
    q(".progress-container").classList.remove("hidden");
    q("#downloadLink").classList.add("hidden");
    // start polling frequently for real-time feel
    if (pollHandle) clearInterval(pollHandle);
    pollHandle = setInterval(() => pollProgress(currentJob), 600);
    // immediate first poll
    pollProgress(currentJob);
  } catch (e) {
    alert("Failed to start download: " + (e.message || e));
  }
}

async function pollProgress(jobId) {
  if (!jobId) return;
  try {
    const res = await fetch(`/api/progress/${jobId}`);
    const d = await res.json();
    if (d.error) {
      q("#progress-text").innerText = "Error: " + d.error;
      q("#progress-bar").style.width = "0%";
      clearInterval(pollHandle);
      return;
    }
    const pct = Number(d.percent) || 0;
    // animate width smoothly
    q("#progress-bar").style.width = Math.min(100, Math.max(0, pct)) + "%";
    q("#progress-text").innerText = `${pct.toFixed(2)}% • ${d.speed || ""}`;

    // set title & download link when available
    if (d.title) {
      q("#title").innerText = d.title;
    }
    if ((d.status === "finished" || pct >= 99.9) && d.filename) {
      clearInterval(pollHandle);
      q("#progress-text").innerText = `100.00% • Done`;
      // reveal download link
      q("#downloadLink").classList.remove("hidden");
      const dl = q("#dl");
      dl.href = `/api/getfile/${jobId}`;
      dl.innerText = `Download "${d.title || d.filename}"`;
      // auto-trigger browser download after tiny delay
      setTimeout(() => { window.location.href = dl.href; }, 600);
    } else if (d.status === "error") {
      clearInterval(pollHandle);
      q("#progress-text").innerText = `Error: ${d.error || "unknown"}`;
    }
  } catch (e) {
    // network hiccup — keep polling
    console.warn("poll error", e);
  }
}
