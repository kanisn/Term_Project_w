const logTargets = [
  "qos_ryu_app",
  "mininet_topo",
  "decision_engine_push_to_ryu",
  "current_network",
  "traffic_video_abr",
  "traffic_file",
];

function startScript(name) {
  fetch(`/api/run/${name}`, { method: "POST" })
    .then((res) => res.json())
    .then((data) => {
      console.log(data);
      if (name === "qos_ryu_app") updateQosStatus();
    });
}

function readyTraffic(name) {
  fetch(`/api/run/${name}`, { method: "POST" })
    .then((res) => res.json())
    .then((data) => {
      if (name === "traffic_video_abr" && data.status) {
        document.getElementById("video-on").disabled = false;
        document.getElementById("video-off").disabled = false;
      }
      if (name === "traffic_file" && data.status) {
        document.getElementById("download-on").disabled = false;
        document.getElementById("download-off").disabled = false;
      }
    });
}

function toggleVideo(isOn) {
  fetch(`/api/video/${isOn ? "start" : "stop"}`, { method: "POST" });
}

function toggleDownload(isOn) {
  fetch(`/api/download/${isOn ? "start" : "stop"}`, { method: "POST" });
}

function refreshLogs() {
  logTargets.forEach((name) => {
    fetch(`/api/logs/${name}`)
      .then((res) => res.json())
      .then((data) => {
        const el = document.getElementById(`log-${name}`);
        if (el) {
          el.textContent = data.logs.join("\n");
          el.scrollTop = el.scrollHeight;
        }
      });
  });
}

let trafficChart;
function setupChart() {
  const ctx = document.getElementById("traffic-chart").getContext("2d");
  trafficChart = new Chart(ctx, {
    type: "line",
    data: {
      labels: [],
      datasets: [
        {
          label: "Video (Mbps)",
          borderColor: "#2b7fff",
          data: [],
          fill: false,
        },
        {
          label: "Download (Mbps)",
          borderColor: "#ff9f1c",
          data: [],
          fill: false,
        },
        {
          label: "Total (Mbps)",
          borderColor: "#1fa28a",
          data: [],
          fill: false,
        },
      ],
    },
    options: {
      animation: false,
      scales: {
        y: { beginAtZero: true },
      },
    },
  });
}

function updateChart() {
  fetch("/api/traffic")
    .then((res) => res.json())
    .then((data) => {
      if (!trafficChart) return;
      trafficChart.data.labels = data.timestamps;
      trafficChart.data.datasets[0].data = data.video;
      trafficChart.data.datasets[1].data = data.download;
      trafficChart.data.datasets[2].data = data.total;
      trafficChart.update();
    });
}

function updateQosStatus() {
  fetch("/api/qos-status")
    .then((res) => res.json())
    .then((data) => {
      const badge = document.getElementById("qos-status");
      badge.textContent = data.status || "IDLE";
      if (data.status === "ACTIVE") {
        badge.classList.add("active");
      } else {
        badge.classList.remove("active");
      }
    });
}

function loadDecisionCsv() {
  fetch("/api/decision-log")
    .then((res) => res.json())
    .then((data) => {
      const container = document.getElementById("decision-table");
      if (!data.rows || data.rows.length === 0) {
        container.innerHTML = "<p>No CSV data found.</p>";
        return;
      }
      const headers = data.rows[0];
      const rows = data.rows.slice(1);
      const table = document.createElement("table");
      const thead = document.createElement("thead");
      const headRow = document.createElement("tr");
      headers.forEach((h) => {
        const th = document.createElement("th");
        th.textContent = h;
        headRow.appendChild(th);
      });
      thead.appendChild(headRow);
      table.appendChild(thead);

      const tbody = document.createElement("tbody");
      rows.forEach((row) => {
        const tr = document.createElement("tr");
        row.forEach((cell) => {
          const td = document.createElement("td");
          td.textContent = cell;
          tr.appendChild(td);
        });
        tbody.appendChild(tr);
      });
      table.appendChild(tbody);
      container.innerHTML = "";
      container.appendChild(table);
    });
}

function poll() {
  refreshLogs();
  updateQosStatus();
  updateChart();
}

setupChart();
loadDecisionCsv();
setInterval(poll, 1000);
