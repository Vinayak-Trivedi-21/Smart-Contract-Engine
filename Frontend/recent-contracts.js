(function () {
    const API_BASE_URL = window.CONTRACT_API_BASE_URL || "http://localhost:8001";
    const CONTRACTS_ENDPOINT = API_BASE_URL.replace(/\/$/, "") + "/contracts";
    const openedStorageKey = "contractEngineRecentlyOpened";
    const MAX_RECENT_OPENED = 5;

    const searchInput = document.getElementById("searchContracts");
    const contractsList = document.getElementById("contractsList");
    const emptyState = document.getElementById("emptyState");
    let searchTimer = null;
    let allContractsCache = [];

    if (!searchInput || !contractsList || !emptyState) {
        return;
    }

    function readOpenedHistory() {
        try {
            const saved = localStorage.getItem(openedStorageKey);
            const parsed = saved ? JSON.parse(saved) : [];
            return Array.isArray(parsed) ? parsed : [];
        } catch (error) {
            return [];
        }
    }

    function writeOpenedHistory(history) {
        localStorage.setItem(openedStorageKey, JSON.stringify(history));
    }

    function rememberOpened(contract) {
        const history = readOpenedHistory().filter(function (item) {
            return item && item.id !== contract.id;
        });

        history.unshift({
            id: contract.id,
            fileName: contract.fileName,
            objectPath: contract.objectPath,
            openUrl: contract.openUrl,
            openedAt: new Date().toISOString(),
            updatedAt: contract.updatedAt
        });

        writeOpenedHistory(history.slice(0, 50));
    }

    async function fetchContracts(queryText, limit) {
        const params = new URLSearchParams();
        params.set("limit", String(limit || 500));
        params.set("offset", "0");

        if (queryText && queryText.trim()) {
            params.set("q", queryText.trim());
        }

        const response = await fetch(CONTRACTS_ENDPOINT + "?" + params.toString(), {
            method: "GET"
        });

        let payload = null;
        try {
            payload = await response.json();
        } catch (error) {
            payload = null;
        }

        if (!response.ok) {
            const serverMessage = payload && (payload.detail || payload.message);
            throw new Error(serverMessage || "Failed to load contracts from backend.");
        }

        return payload && Array.isArray(payload.items) ? payload.items : [];
    }

    function formatDate(value) {
        if (!value) {
            return "Not provided";
        }

        const date = new Date(value);
        return Number.isNaN(date.getTime()) ? value : date.toLocaleDateString();
    }

    function escapeHtml(value) {
        return String(value || "").replace(/[&<>"']/g, function (character) {
            const entities = {
                "&": "&amp;",
                "<": "&lt;",
                ">": "&gt;",
                '"': "&quot;",
                "'": "&#39;"
            };

            return entities[character];
        });
    }

    function createCard(contract, showOpenedAt) {
        const article = document.createElement("article");
        article.className = "contract-card";

        const updatedLabel = formatDate(contract.updatedAt);
        const openedAtLabel = showOpenedAt ? formatDate(contract.openedAt) : null;
        const openAction = contract.openUrl
            ? "<a class=\"dashboard-btn\" target=\"_blank\" rel=\"noopener noreferrer\" data-action=\"open-contract\" href=\"" + escapeHtml(contract.openUrl) + "\">Open Contract</a>"
            : "<button class=\"dashboard-btn\" type=\"button\" disabled>Open unavailable</button>";

        const openedMarkup = openedAtLabel
            ? "<li><strong>Opened:</strong> " + escapeHtml(openedAtLabel) + "</li>"
            : "";

        article.innerHTML = [
            "<div class=\"contract-card-header\">",
            "<div>",
            "<h2>" + escapeHtml(contract.fileName || "Contract") + "</h2>",
            "<p class=\"contract-meta\">" + escapeHtml(contract.objectPath || "") + "</p>",
            "</div>",
            "<p class=\"contract-meta\">Updated " + escapeHtml(updatedLabel) + "</p>",
            "</div>",
            "<ul class=\"contract-details\">",
            "<li><strong>File:</strong> " + escapeHtml(contract.fileName) + "</li>",
            "<li><strong>Object Path:</strong> " + escapeHtml(contract.objectPath) + "</li>",
            "<li><strong>Record ID:</strong> " + escapeHtml(contract.id) + "</li>",
            openedMarkup,
            "</ul>",
            "<div class=\"action-row\">",
            openAction,
            "</div>"
        ].join("");

        const openLink = article.querySelector("[data-action='open-contract']");
        if (openLink) {
            openLink.addEventListener("click", function () {
                rememberOpened(contract);
            });
        }

        return article;
    }

    function renderEmptyState(message) {
        contractsList.hidden = true;
        emptyState.hidden = false;
        emptyState.querySelector("p").textContent = message;
    }

    function renderContracts(contracts, showOpenedAt) {
        contractsList.innerHTML = "";

        if (!contracts.length) {
            renderEmptyState("No contracts match that search.");
            return;
        }

        contracts.forEach(function (contract) {
            contractsList.appendChild(createCard(contract, showOpenedAt));
        });

        contractsList.hidden = false;
        emptyState.hidden = true;
    }

    function renderRecentOpenedFromCache() {
        const openedHistory = readOpenedHistory();
        if (!openedHistory.length) {
            renderEmptyState("No opened contracts yet. Search and open a contract to populate this view.");
            return;
        }

        const contractsById = {};
        allContractsCache.forEach(function (contract) {
            contractsById[contract.id] = contract;
        });

        const recentContracts = openedHistory.slice(0, MAX_RECENT_OPENED).map(function (opened) {
            const liveContract = contractsById[opened.id];
            if (liveContract) {
                return {
                    id: liveContract.id,
                    fileName: liveContract.fileName,
                    objectPath: liveContract.objectPath,
                    openUrl: liveContract.openUrl || opened.openUrl,
                    updatedAt: liveContract.updatedAt,
                    openedAt: opened.openedAt
                };
            }

            return {
                id: opened.id,
                fileName: opened.fileName,
                objectPath: opened.objectPath,
                openUrl: opened.openUrl,
                updatedAt: opened.updatedAt,
                openedAt: opened.openedAt
            };
        });

        renderContracts(recentContracts, true);
    }

    async function refreshRecentOpenedView() {
        try {
            allContractsCache = await fetchContracts("", 1000);
            renderRecentOpenedFromCache();
        } catch (error) {
            renderEmptyState(error.message || "Could not load contracts from backend.");
        }
    }

    async function runSearch(searchText) {
        if (!searchText.trim()) {
            renderRecentOpenedFromCache();
            return;
        }

        try {
            const matches = await fetchContracts(searchText, 200);
            if (!matches.length) {
                renderEmptyState("No contracts match that search.");
                return;
            }

            renderContracts(matches, false);
        } catch (error) {
            renderEmptyState(error.message || "Search failed. Please try again.");
        }
    }

    searchInput.addEventListener("input", function () {
        window.clearTimeout(searchTimer);
        searchTimer = window.setTimeout(function () {
            runSearch(searchInput.value);
        }, 250);
    });

    refreshRecentOpenedView();
})();
