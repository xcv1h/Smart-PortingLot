// Smart Parking dashboard, themed as a Cisco switch terminal.
// Parking slots are switch ports, a parked car is a port in the "up" state.

const POLL_MS = 2000;       // how often we ask the API for the lot state
const MAX_LOG_LINES = 200;  // old log lines are dropped so the page never grows forever

const tableEl = document.getElementById("table");
const statusEl = document.getElementById("status");
const logEl = document.getElementById("log");
const cmdEl = document.getElementById("cmd");

// Last state received from GET /api/ports.
let ports = [];
let capacity = 0;
// False until the first successful poll. The first poll only records the
// state: without this, every page reload would log every parked car again.
let hasState = false;
// True while the API cannot be reached, so we log the problem only once.
let apiDown = false;


// ---------------------------------------------------------------------------
// API access. Every call goes through api(), which returns
// { status: <HTTP status code>, data: <parsed JSON or null> }.
// ---------------------------------------------------------------------------

async function realApi(method, path, body) {
    // "no-store": never answer a poll from the browser cache.
    const options = { method: method, cache: "no-store" };
    if (body !== undefined) {
        options.headers = { "Content-Type": "application/json" };
        options.body = JSON.stringify(body);
    }
    // A URL without a host goes to the server the page came from (nginx),
    // which forwards /api/ to the backend. Same origin, so no CORS.
    const response = await fetch("/api" + path, options);
    let data = null;
    try {
        data = await response.json();
    } catch (error) {
        // Some error responses (a 502 from nginx, for example) are not JSON.
    }
    return { status: response.status, data: data };
}


// ===== MOCK MODE: START =====================================================
// An in-memory fake of the three endpoints, used when the page is opened with
// ?mock=1 so the frontend can be developed without the backend.
// To remove it: delete this block and the "useMock" lines right below it.

const MOCK_CAPACITY = 8;
const MOCK_FEE_PER_HOUR = 12;
// One entry per port: null when free, or { plate, entered_at }.
const mockSlots = new Array(MOCK_CAPACITY).fill(null);
// Start with two cars so the table is not empty.
mockSlots[0] = { plate: "12345678", entered_at: new Date(Date.now() - 90 * 60 * 1000).toISOString() };
mockSlots[3] = { plate: "7654321", entered_at: new Date(Date.now() - 5 * 60 * 1000).toISOString() };

function mockPortName(index) {
    return "Gi0/" + (index + 1);
}

async function mockApi(method, path, body) {
    if (method === "GET" && path === "/ports") {
        const list = mockSlots.map(function (slot, index) {
            return {
                port: mockPortName(index),
                plate: slot ? slot.plate : null,
                entered_at: slot ? slot.entered_at : null
            };
        });
        return { status: 200, data: { capacity: MOCK_CAPACITY, ports: list } };
    }

    if (method === "POST" && path === "/cars") {
        const plate = body.plate;
        if (!/^\d{7,8}$/.test(plate)) {
            return { status: 422, data: null };
        }
        if (mockSlots.some(function (slot) { return slot && slot.plate === plate; })) {
            return { status: 409, data: null };
        }
        const free = mockSlots.indexOf(null);
        if (free === -1) {
            return { status: 507, data: null };
        }
        mockSlots[free] = { plate: plate, entered_at: new Date().toISOString() };
        return {
            status: 201,
            data: { port: mockPortName(free), plate: plate, entered_at: mockSlots[free].entered_at }
        };
    }

    if (method === "DELETE" && path.startsWith("/cars/")) {
        const plate = decodeURIComponent(path.slice("/cars/".length));
        const index = mockSlots.findIndex(function (slot) { return slot && slot.plate === plate; });
        if (index === -1) {
            return { status: 404, data: null };
        }
        const seconds = Math.floor((Date.now() - Date.parse(mockSlots[index].entered_at)) / 1000);
        const fee = Math.round(seconds / 3600 * MOCK_FEE_PER_HOUR * 100) / 100;
        mockSlots[index] = null;
        return {
            status: 200,
            data: { port: mockPortName(index), plate: plate, duration_seconds: seconds, fee: fee }
        };
    }

    return { status: 404, data: null };
}

const useMock = new URLSearchParams(window.location.search).get("mock") === "1";
// ===== MOCK MODE: END =======================================================

const api = useMock ? mockApi : realApi;


// ---------------------------------------------------------------------------
// Formatting helpers
// ---------------------------------------------------------------------------

// 7 digits -> 12-345-67, 8 digits -> 123-45-678 (Israeli plate formats).
function formatPlate(plate) {
    if (plate.length === 7) {
        return plate.slice(0, 2) + "-" + plate.slice(2, 5) + "-" + plate.slice(5);
    }
    if (plate.length === 8) {
        return plate.slice(0, 3) + "-" + plate.slice(3, 5) + "-" + plate.slice(5);
    }
    return plate;
}

// 5400 -> "01:30:00"
function formatDuration(totalSeconds) {
    const seconds = Math.max(0, Math.floor(totalSeconds));
    const h = String(Math.floor(seconds / 3600)).padStart(2, "0");
    const m = String(Math.floor(seconds % 3600 / 60)).padStart(2, "0");
    const s = String(seconds % 60).padStart(2, "0");
    return h + ":" + m + ":" + s;
}

function isPalindrome(text) {
    return text === text.split("").reverse().join("");
}


// ---------------------------------------------------------------------------
// Drawing
// ---------------------------------------------------------------------------

// The table is plain text: columns line up because every cell is padded with
// spaces to a fixed width and the font is monospace.
function drawTable() {
    tableEl.textContent = "";

    const header = document.createElement("div");
    header.textContent = "Port".padEnd(10) + "Status".padEnd(9) + "Plate".padEnd(13) + "Uptime";
    tableEl.append(header);

    for (const p of ports) {
        const row = document.createElement("div");
        const parked = p.plate !== null;

        // Only the status word is coloured, so it needs its own <span>.
        const status = document.createElement("span");
        status.textContent = (parked ? "up" : "down").padEnd(9);
        if (parked) {
            status.className = "up";
        }

        let plate = "-";
        let uptime = "-";
        if (parked) {
            plate = formatPlate(p.plate);
            // Uptime is counted in the browser from entered_at, so it keeps
            // ticking between polls.
            uptime = formatDuration((Date.now() - Date.parse(p.entered_at)) / 1000);
            if (isPalindrome(p.plate)) {
                row.className = "mirrored";
            }
        }

        // textContent / append with strings never interprets HTML, so a
        // strange plate value from the API cannot inject markup.
        row.append(p.port.padEnd(10), status, plate.padEnd(13), uptime);
        tableEl.append(row);
    }
}

function drawStatus() {
    if (apiDown) {
        statusEl.textContent = "% API unreachable, retrying";
        statusEl.className = "alarm";
        return;
    }
    const used = ports.filter(function (p) { return p.plate !== null; }).length;
    const full = capacity > 0 && used >= capacity;
    statusEl.textContent = "Ports in use: " + used + "/" + capacity + (full ? "   LOT FULL - no free ports" : "");
    statusEl.className = full ? "alarm" : "";
}

// Add one line to the scrolling log and keep the newest line visible.
function print(text, className) {
    const line = document.createElement("div");
    line.textContent = text;
    if (className) {
        line.className = className;
    }
    logEl.append(line);
    while (logEl.childElementCount > MAX_LOG_LINES) {
        logEl.firstElementChild.remove();
    }
    logEl.scrollTop = logEl.scrollHeight;
}

// IOS puts a timestamp in front of syslog lines: *12:00:05: %PARK-5-UPDOWN: ...
function syslog(message, className) {
    const time = new Date().toLocaleTimeString("en-GB");
    print("*" + time + ": " + message, className);
}


// ---------------------------------------------------------------------------
// Polling
// ---------------------------------------------------------------------------

// Compare the previous state with the new one and log what changed.
function logChanges(oldPorts, newPorts, newCapacity) {
    const oldPlates = {};
    for (const p of oldPorts) {
        oldPlates[p.port] = p.plate;
    }

    for (const p of newPorts) {
        const before = oldPlates[p.port] || null;
        if (before === p.plate) {
            continue;
        }
        // A different plate on the same port means one car left and another
        // arrived between two polls: log both events.
        if (before !== null) {
            syslog("%PARK-5-UPDOWN: Slot " + p.port + ", changed state to down (plate " + formatPlate(before) + ")");
        }
        if (p.plate !== null) {
            syslog("%PARK-5-UPDOWN: Slot " + p.port + ", changed state to up (plate " + formatPlate(p.plate) + ")");
        }
    }

    const usedBefore = oldPorts.filter(function (p) { return p.plate !== null; }).length;
    const usedNow = newPorts.filter(function (p) { return p.plate !== null; }).length;
    // Log only at the moment the lot becomes full, not on every poll while full.
    if (usedNow >= newCapacity && usedBefore < newCapacity) {
        syslog("%PARK-2-FULL: No free ports", "error");
    }
}

async function poll() {
    let result = null;
    try {
        result = await api("GET", "/ports");
    } catch (error) {
        // fetch throws when there is no answer at all (network down).
    }

    if (result === null || result.status !== 200 || result.data === null) {
        if (!apiDown) {
            apiDown = true;
            syslog("%PARK-3-APIDOWN: Cannot reach the API", "error");
        }
        drawStatus();
        return;
    }

    if (apiDown) {
        apiDown = false;
        syslog("%PARK-5-APIUP: API is reachable again");
    }
    if (hasState) {
        logChanges(ports, result.data.ports, result.data.capacity);
    }
    ports = result.data.ports;
    capacity = result.data.capacity;
    hasState = true;
    drawTable();
    drawStatus();
}


// ---------------------------------------------------------------------------
// Commands
// ---------------------------------------------------------------------------

function printHelp() {
    print("  show parking-table   redraw the table now");
    print("  shutdown <port>      release the car on that port, e.g. shutdown Gi0/3");
    print("  park <plate>         park a car manually, e.g. park 12345678");
    print("  help                 list commands");
}

// Errors from api() calls made by a command (not by the poll).
function printApiFailure(result) {
    if (result === null) {
        print("% API unreachable", "error");
    } else {
        print("% Unexpected API response (HTTP " + result.status + ")", "error");
    }
}

async function callApi(method, path, body) {
    try {
        return await api(method, path, body);
    } catch (error) {
        return null;
    }
}

async function park(plate) {
    const result = await callApi("POST", "/cars", { plate: plate });
    if (result !== null && result.status === 201) {
        print("Plate " + formatPlate(result.data.plate) + " parked on " + result.data.port);
    } else if (result !== null && result.status === 409) {
        print("% Plate " + plate + " is already parked", "error");
    } else if (result !== null && result.status === 422) {
        print("% Invalid plate: must be 7 or 8 digits", "error");
    } else if (result !== null && result.status === 507) {
        print("% No free ports", "error");
    } else {
        printApiFailure(result);
    }
}

async function shutdown(portName) {
    // The API releases cars by plate, so look the port up in the last polled
    // state to find which plate is on it. IOS ignores case, so do we.
    const port = ports.find(function (p) {
        return p.port.toLowerCase() === portName.toLowerCase();
    });
    if (port === undefined) {
        print("% Invalid interface " + portName, "error");
        return;
    }
    if (port.plate === null) {
        print("% " + port.port + " is already down", "error");
        return;
    }

    const result = await callApi("DELETE", "/cars/" + encodeURIComponent(port.plate));
    if (result !== null && result.status === 200) {
        print(result.data.port + " released plate " + formatPlate(result.data.plate)
            + ", duration " + formatDuration(result.data.duration_seconds)
            + ", fee " + result.data.fee.toFixed(2));
    } else if (result !== null && result.status === 404) {
        print("% Plate " + formatPlate(port.plate) + " not found", "error");
    } else {
        printApiFailure(result);
    }
}

async function runCommand(line) {
    // Echo what was typed, like a terminal does.
    print("parking-sw#" + line);

    const words = line.trim().split(/\s+/);
    const command = words[0].toLowerCase();

    if (command === "") {
        return;  // Enter on an empty line just prints a new prompt.
    }

    if (command === "help" && words.length === 1) {
        printHelp();
    } else if (command === "show" && words.length === 1) {
        print("% Incomplete command.", "error");
    } else if (command === "show" && words.length === 2 && words[1].toLowerCase() === "parking-table") {
        await poll();
    } else if ((command === "shutdown" || command === "park") && words.length === 1) {
        print("% Incomplete command.", "error");
    } else if (command === "shutdown" && words.length === 2) {
        await shutdown(words[1]);
        await poll();  // show the result now instead of waiting for the next poll
    } else if (command === "park" && words.length === 2) {
        await park(words[1]);
        await poll();
    } else {
        print("% Invalid input detected", "error");
    }
}


// ---------------------------------------------------------------------------
// Start
// ---------------------------------------------------------------------------

cmdEl.addEventListener("keydown", function (event) {
    if (event.key === "Enter") {
        const line = cmdEl.value;
        cmdEl.value = "";
        runCommand(line);
    }
});

// Clicking anywhere on the "terminal" puts the cursor back on the prompt.
// (Not while selecting text, so log lines can still be copied.)
document.addEventListener("click", function () {
    if (window.getSelection().toString() === "") {
        cmdEl.focus();
    }
});

if (useMock) {
    print("Mock mode: using fake in-memory data, not the real API.");
}
print("Type help to list commands.");

poll();
setInterval(poll, POLL_MS);
// Redraw every second so the Uptime column ticks between polls.
setInterval(drawTable, 1000);
