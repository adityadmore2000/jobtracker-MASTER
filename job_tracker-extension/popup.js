const API_BASE_URL = "http://127.0.0.1:8000";

const pageTitleElement = document.getElementById("pageTitle");
const pageUrlElement = document.getElementById("pageUrl");
const statusElement = document.getElementById("status");
const sendButton = document.getElementById("sendButton");

let currentPage = null;

function setStatus(message, type = "") {
  statusElement.textContent = message;
  statusElement.className = type ? `status ${type}` : "status";
}

function isHttpUrl(url) {
  try {
    const parsedUrl = new URL(url);
    return parsedUrl.protocol === "http:" || parsedUrl.protocol === "https:";
  } catch {
    return false;
  }
}

async function loadActiveTab() {
  try {
    const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
    const title = tab?.title || "";
    const url = tab?.url || "";

    pageTitleElement.textContent = title || "Untitled page";
    pageUrlElement.textContent = url || "No URL available";

    if (!isHttpUrl(url)) {
      currentPage = null;
      sendButton.disabled = true;
      setStatus("Invalid page URL", "error");
      return;
    }

    currentPage = { page_title: title, url };
    sendButton.disabled = false;
    setStatus("Idle");
  } catch {
    currentPage = null;
    sendButton.disabled = true;
    pageTitleElement.textContent = "Unable to read current tab";
    pageUrlElement.textContent = "";
    setStatus("Invalid page URL", "error");
  }
}

async function sendCurrentPage() {
  if (!currentPage) {
    return;
  }

  sendButton.disabled = true;
  setStatus("Sending...");

  try {
    const response = await fetch(`${API_BASE_URL}/browser-context`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(currentPage),
    });

    if (!response.ok) {
      throw new Error("Unable to connect to local tracker");
    }

    setStatus("Captured successfully", "success");
  } catch {
    setStatus("Unable to connect to local tracker", "error");
  } finally {
    sendButton.disabled = false;
  }
}

sendButton.addEventListener("click", sendCurrentPage);
loadActiveTab();
