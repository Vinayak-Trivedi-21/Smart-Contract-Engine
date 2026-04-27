(function () {
    const API_BASE_URL = window.CONTRACT_API_BASE_URL || "http://localhost:8001";
    const SEARCH_ENDPOINT = API_BASE_URL.replace(/\/$/, "") + "/contracts/search";

    const searchInput = document.getElementById("searchContracts");
    const contractsList = document.getElementById("contractsList");
    const emptyState = document.getElementById("emptyState");
    let searchTimer = null;

    if (!searchInput || !contractsList || !emptyState) {
        return;
    }

    async function fetchContracts(queryText, limit) {
        const params = new URLSearchParams();
        params.set("limit", String(limit || 50));

        if (queryText && queryText.trim()) {
            params.set("title", queryText.trim());
        }

        const response = await fetch(SEARCH_ENDPOINT + "?" + params.toString(), {
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

    function normalizeContract(contract) {
        return {
            id: contract && (contract.id || contract.contract_id) ? String(contract.id || contract.contract_id) : "",
            title: contract && (contract.title || contract.filename) ? String(contract.title || contract.filename) : "Contract",
            contractType: contract && (contract.contractType || contract.contract_type) ? String(contract.contractType || contract.contract_type) : "Other",
            createdAt: contract ? (contract.createdAt || contract.created_at) : "",
            status: contract && contract.status ? String(contract.status) : ""
        };
    }

    function createCard(contract) {
        const article = document.createElement("article");
        article.className = "contract-card";

        const item = normalizeContract(contract);
        const createdLabel = formatDate(item.createdAt);
        const statusLabel = item.status || "Not set";

        article.innerHTML = [
            "<div class=\"contract-card-header\">",
            "<div>",
            "<h2>" + escapeHtml(item.title) + "</h2>",
            "<p class=\"contract-meta\">" + escapeHtml(item.contractType) + "</p>",
            "</div>",
            "<p class=\"contract-meta\">Created " + escapeHtml(createdLabel) + "</p>",
            "</div>",
            "<ul class=\"contract-details\">",
            "<li><strong>Title:</strong> " + escapeHtml(item.title) + "</li>",
            "<li><strong>Type:</strong> " + escapeHtml(item.contractType) + "</li>",
            "<li><strong>Status:</strong> " + escapeHtml(statusLabel) + "</li>",
            "<li><strong>Contract ID:</strong> " + escapeHtml(item.id) + "</li>",
            "</ul>",
            "<div class=\"action-row\">",
            "<button class=\"dashboard-btn\" type=\"button\" data-action=\"use-for-review\">Use for Review</button>",
            "</div>"
        ].join("");

        const reviewButton = article.querySelector("[data-action='use-for-review']");
        if (reviewButton) {
            reviewButton.addEventListener("click", function () {
                if (!item.id) {
                    return;
                }

                localStorage.setItem("contractEngineSelectedReviewContractId", item.id);
                window.location.href = "analyse-contract.html";
            });
        }

        return article;
    }

    function renderEmptyState(message) {
        contractsList.hidden = true;
        emptyState.hidden = false;
        emptyState.querySelector("p").textContent = message;
    }

    function renderContracts(contracts) {
        contractsList.innerHTML = "";

        if (!contracts.length) {
            renderEmptyState("No contracts match that search.");
            return;
        }

        contracts.forEach(function (contract) {
            contractsList.appendChild(createCard(contract));
        });

        contractsList.hidden = false;
        emptyState.hidden = true;
    }

    async function loadContracts(searchText) {
        try {
            const matches = await fetchContracts(searchText, 100);
            if (!matches.length) {
                renderEmptyState("No contracts found.");
                return;
            }

            renderContracts(matches);
        } catch (error) {
            renderEmptyState(error.message || "Could not load contracts from backend.");
        }
    }

    searchInput.addEventListener("input", function () {
        window.clearTimeout(searchTimer);
        searchTimer = window.setTimeout(function () {
            loadContracts(searchInput.value.trim());
        }, 250);
    });

    loadContracts("");
})();
