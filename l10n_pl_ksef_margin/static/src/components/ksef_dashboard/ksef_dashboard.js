/** @odoo-module **/

import { registry } from "@web/core/registry";
import { Component, onWillStart, useState } from "@odoo/owl";
import { useService } from "@web/core/utils/hooks";
import { _t } from "@web/core/l10n/translation";
import { localization } from "@web/core/l10n/localization";

/**
 * KsefDashboard — main accountant workspace for KSeF integration.
 *
 * Sections (top → bottom):
 *   1. Hero Action Bar — bulk actions (send all, sync, fetch incoming, export)
 *   2. KPI cards row — accepted / waiting / not-sent / rejected (clickable)
 *   3. Inbox / TODO — actionable items needing attention (NEW)
 *   4. Activity timeline chart — invoices by date with hover tooltip
 *   5. Recent operations + buffer alerts (right column)
 *
 * All data loaded in parallel via Promise.all. Status counts cached in state.
 * Click-through enabled on every card → opens filtered list view.
 */
export class KsefDashboard extends Component {
    static template = "l10n_pl_ksef_margin.KsefDashboard";
    static props = {
        action: { type: Object, optional: true },
        actionId: { optional: true },
        className: { type: String, optional: true },
        globalState: { optional: true },
        "*": true,
    };

    setup() {
        this.orm = useService("orm");
        this.action = useService("action");
        this.notification = useService("notification");

        this.state = useState({
            // Status counts
            stats: {
                draft: 0,
                waiting: 0,
                accepted: 0,
                rejected: 0,
                acceptedMonth: 0,
                acceptedMonthAmount: 0,
            },
            // Inbox: action items
            inbox: {
                rejectedItems: [],
                waitingStuck: 0,        // waiting > 1h
                draftOld: 0,            // draft > 24h
                bufferCount: 0,
            },
            // Compliance countdown
            compliance: { daysToMandate: 0, mandatoryDate: "1.04.2026" },
            pipelineTotal: 0,
            // Recent operations
            recentMoves: [],
            // Chart
            chartPoints: [],
            chartDays: 30,
            // UI states
            loaded: false,
            sending: false,
            syncing: false,
            // Tooltip
            tooltip: { visible: false, xPct: 0, yPct: 0, label: "", count: 0, svgX: 0, svgY: 0 },
        });

        onWillStart(() => this._loadAll());
    }

    // ────────────────────────────────────────────────────────────────────
    // Data loading
    // ────────────────────────────────────────────────────────────────────

    async _loadAll() {
        const today = new Date();
        const fromDate = this._dateNDaysAgo(this.state.chartDays);
        const firstOfMonth = new Date(today.getFullYear(), today.getMonth(), 1)
            .toISOString().split("T")[0];
        const oneDayAgo = this._dateNDaysAgo(1);
        const oneHourAgo = new Date(Date.now() - 3600 * 1000).toISOString().slice(0, 19);

        const baseDomain = [
            ["move_type", "in", ["out_invoice", "out_refund"]],
            ["state", "=", "posted"],
        ];

        const [
            statusGroups,
            chartMoves,
            acceptedMonthData,
            recentMoves,
            bufferCount,
            rejectedItems,
            waitingStuck,
            draftOld,
        ] = await Promise.all([
            // 1. Counts by status
            this.orm.readGroup(
                "account.move",
                baseDomain,
                ["l10n_pl_ksef_status"],
                ["l10n_pl_ksef_status"]
            ),
            // 2. Chart data (invoice dates)
            this.orm.searchRead(
                "account.move",
                [...baseDomain, ["invoice_date", ">=", fromDate], ["l10n_pl_ksef_status", "!=", false]],
                ["invoice_date", "l10n_pl_ksef_status"],
                { limit: 1000 }
            ),
            // 3. Accepted this month with sum
            this.orm.readGroup(
                "account.move",
                [
                    ...baseDomain,
                    ["l10n_pl_ksef_status", "=", "accepted"],
                    ["invoice_date", ">=", firstOfMonth],
                ],
                ["amount_total:sum"],
                []
            ),
            // 4. Recent operations (last 8)
            this.orm.searchRead(
                "account.move",
                baseDomain,
                ["id", "name", "partner_id", "amount_total", "currency_id",
                 "l10n_pl_ksef_status", "invoice_date", "write_date"],
                { limit: 8, order: "write_date desc" }
            ),
            // 5. Buffer (incoming)
            this.orm.searchCount("ksef.vendor.buffer", [["state", "=", "new"]]),
            // 6. Rejected list (top 5 most recent) — include reason from KSeF
            this.orm.searchRead(
                "account.move",
                [...baseDomain, ["l10n_pl_ksef_status", "=", "rejected"]],
                ["id", "name", "partner_id", "amount_total", "currency_id",
                 "l10n_pl_ksef_error_code", "l10n_pl_ksef_error_message",
                 "l10n_pl_ksef_rejected_at"],
                { limit: 5, order: "l10n_pl_ksef_rejected_at desc, write_date desc" }
            ),
            // 7. Stuck waiting (>1h)
            this.orm.searchCount("account.move", [
                ...baseDomain,
                ["l10n_pl_ksef_status", "=", "waiting"],
                ["write_date", "<=", oneHourAgo],
            ]),
            // 8. Old drafts (>24h)
            this.orm.searchCount("account.move", [
                ...baseDomain,
                ["l10n_pl_ksef_status", "=", "draft"],
                ["invoice_date", "<=", oneDayAgo],
            ]),
        ]);

        // Status counts
        const stats = {
            draft: 0, waiting: 0, accepted: 0, rejected: 0,
            acceptedMonth: 0, acceptedMonthAmount: 0,
        };
        for (const g of statusGroups) {
            const s = g.l10n_pl_ksef_status || "draft";
            if (s in stats) stats[s] = g.l10n_pl_ksef_status_count;
        }
        if (acceptedMonthData.length) {
            stats.acceptedMonth = acceptedMonthData[0].__count || 0;
            stats.acceptedMonthAmount = acceptedMonthData[0].amount_total || 0;
        }

        // Build chart points
        const dayMap = {};
        for (const m of chartMoves) {
            if (m.invoice_date) {
                dayMap[m.invoice_date] = (dayMap[m.invoice_date] || 0) + 1;
            }
        }
        const chartPoints = this._buildDateRange(fromDate, today, dayMap);

        // Compliance countdown to KSeF mandatory date for all VAT taxpayers (1.04.2026).
        // Source: https://www.gov.pl/web/finanse/krajowy-system-e-faktur
        const mandatoryDate = new Date(2026, 3, 1); // April 1, 2026 (month is 0-indexed)
        const today2 = new Date();
        const daysToMandate = Math.ceil((mandatoryDate - today2) / (1000 * 60 * 60 * 24));

        // Pipeline totals (for header viz)
        const pipelineTotal = stats.draft + stats.waiting + stats.accepted + stats.rejected;

        Object.assign(this.state, {
            stats,
            chartPoints,
            recentMoves,
            inbox: { rejectedItems, waitingStuck, draftOld, bufferCount },
            compliance: { daysToMandate, mandatoryDate: "1.04.2026" },
            pipelineTotal,
            loaded: true,
        });
    }

    _dateNDaysAgo(n) {
        const d = new Date();
        d.setDate(d.getDate() - n);
        return d.toISOString().split("T")[0];
    }

    /** Convert Odoo lang code (pl_PL) to BCP 47 locale (pl-PL) for Intl APIs. */
    get _locale() {
        const lang = (localization.langName || "pl_PL").replace("_", "-");
        return lang;
    }

    _buildDateRange(fromStr, toDate, dayMap) {
        const result = [];
        const cur = new Date(fromStr);
        while (cur <= toDate) {
            const key = cur.toISOString().split("T")[0];
            result.push({
                label: cur.toLocaleDateString(this._locale, { day: "numeric", month: "short" }),
                count: dayMap[key] || 0,
            });
            cur.setDate(cur.getDate() + 1);
        }
        return result;
    }

    // ────────────────────────────────────────────────────────────────────
    // Chart computation (monotone cubic interpolation)
    // ────────────────────────────────────────────────────────────────────

    get chartSvg() {
        const pts = this.state.chartPoints;
        if (!pts.length) return null;

        // Side padding on X-axis must accommodate widest tick label so last
        // date (e.g. "2 maj") is not clipped by the SVG viewport edge.
        // Average label width: ~32px (font-size 9 × ~6 chars). Half-width = 16.
        // Same on left to prevent first label clipping.
        const W = 600, H = 130;
        const pad = { l: 36, r: 24, t: 16, b: 28 };
        const cW = W - pad.l - pad.r;
        const cH = H - pad.t - pad.b;
        const maxVal = Math.max(...pts.map((p) => p.count), 1);
        const baseline = pad.t + cH;

        const coords = pts.map((p, i) => ({
            x: +(pad.l + (i / (pts.length - 1 || 1)) * cW).toFixed(2),
            y: +(pad.t + cH * (1 - p.count / maxVal)).toFixed(2),
            count: p.count,
            label: p.label,
            idx: i,
        }));

        // Fritsch-Carlson monotone cubic — preserves monotonicity (no overshoot)
        const monotonePath = (points) => {
            const n = points.length;
            if (n === 1) return `M ${points[0].x},${points[0].y}`;
            if (n === 2) return `M ${points[0].x},${points[0].y} L ${points[1].x},${points[1].y}`;

            const dx = [], dy = [], slope = [];
            for (let i = 0; i < n - 1; i++) {
                dx[i] = points[i + 1].x - points[i].x;
                dy[i] = points[i + 1].y - points[i].y;
                slope[i] = dy[i] / dx[i];
            }
            const t = new Array(n);
            t[0] = slope[0]; t[n - 1] = slope[n - 2];
            for (let i = 1; i < n - 1; i++) {
                t[i] = slope[i - 1] * slope[i] <= 0 ? 0 : (slope[i - 1] + slope[i]) / 2;
            }
            for (let i = 0; i < n - 1; i++) {
                if (Math.abs(slope[i]) < 1e-10) { t[i] = t[i + 1] = 0; }
                else {
                    const a = t[i] / slope[i], b = t[i + 1] / slope[i];
                    const s = a * a + b * b;
                    if (s > 9) {
                        const tau = 3 / Math.sqrt(s);
                        t[i] = tau * a * slope[i];
                        t[i + 1] = tau * b * slope[i];
                    }
                }
            }
            let d = `M ${points[0].x},${points[0].y}`;
            for (let i = 0; i < n - 1; i++) {
                const cp1x = +(points[i].x + dx[i] / 3).toFixed(2);
                const cp1y = +(points[i].y + t[i] * dx[i] / 3).toFixed(2);
                const cp2x = +(points[i + 1].x - dx[i] / 3).toFixed(2);
                const cp2y = +(points[i + 1].y - t[i + 1] * dx[i] / 3).toFixed(2);
                d += ` C ${cp1x},${cp1y} ${cp2x},${cp2y} ${points[i + 1].x},${points[i + 1].y}`;
            }
            return d;
        };

        const linePath = monotonePath(coords);
        const last = coords[coords.length - 1];
        const areaPath = `${linePath} L ${last.x},${baseline} L ${coords[0].x},${baseline} Z`;
        // Denser X-axis ticks: ~10 labels instead of ~6 so dates don't look
        // stretched/sparse on the bottom row. Always include first + last.
        const step = Math.max(1, Math.round(pts.length / 10));
        const ticks = coords.filter((_, i) => i % step === 0 || i === coords.length - 1);

        // Y-axis: for small maxVals just show 0 + max (integer-clean). For
        // larger ranges add a midpoint rounded to a "nice" number so we don't
        // get oddities like "2" sitting between 0 and 3.
        const yTicks = [
            { y: pad.t, value: maxVal },
            { y: baseline, value: 0 },
        ];
        if (maxVal >= 4) {
            yTicks.splice(1, 0, {
                y: pad.t + Math.round(cH / 2),
                value: Math.round(maxVal / 2),
            });
        }

        return { linePath, areaPath, baseline, ticks, yTicks, W, H, pad, cW, cH, coords };
    }

    // ────────────────────────────────────────────────────────────────────
    // Chart interaction
    // ────────────────────────────────────────────────────────────────────

    onPointEnter(pt) {
        const W = 600, H = 130;
        Object.assign(this.state.tooltip, {
            visible: true,
            xPct: +(pt.x / W * 100).toFixed(1),
            yPct: +(pt.y / H * 100).toFixed(1),
            label: pt.label,
            count: pt.count,
            svgX: pt.x,
            svgY: pt.y,
        });
    }

    onChartLeave() {
        this.state.tooltip.visible = false;
    }

    async onSetRange(days) {
        if (this.state.chartDays === days) return;
        this.state.chartDays = days;
        this.state.tooltip.visible = false;
        const fromDate = this._dateNDaysAgo(days);
        const chartMoves = await this.orm.searchRead(
            "account.move",
            [
                ["move_type", "in", ["out_invoice", "out_refund"]],
                ["state", "=", "posted"],
                ["invoice_date", ">=", fromDate],
            ],
            ["invoice_date"],
            { limit: 2000 }
        );
        const dayMap = {};
        for (const m of chartMoves) {
            if (m.invoice_date) dayMap[m.invoice_date] = (dayMap[m.invoice_date] || 0) + 1;
        }
        this.state.chartPoints = this._buildDateRange(fromDate, new Date(), dayMap);
    }

    // ────────────────────────────────────────────────────────────────────
    // Navigation actions
    // ────────────────────────────────────────────────────────────────────

    openFiltered(status) {
        const ctxMap = {
            accepted: { search_default_ksef_accepted: 1 },
            waiting: { search_default_ksef_waiting: 1 },
            rejected: { search_default_ksef_rejected: 1 },
            draft: { search_default_ksef_not_sent: 1 },
        };
        this.action.doAction("l10n_pl_ksef_margin.action_ksef_dashboard", {
            additionalContext: status ? ctxMap[status] || {} : {},
        });
    }

    openMove(moveId) {
        this.action.doAction({
            type: "ir.actions.act_window",
            res_model: "account.move",
            res_id: moveId,
            view_mode: "form",
            views: [[false, "form"]],
        });
    }

    openBuffer() {
        this.action.doAction("l10n_pl_ksef_margin.action_ksef_vendor_buffer");
    }

    // ────────────────────────────────────────────────────────────────────
    // Bulk actions
    // ────────────────────────────────────────────────────────────────────

    async onSendAll() {
        if (this.state.sending) return;
        this.state.sending = true;
        try {
            const ids = await this.orm.search("account.move", [
                ["move_type", "in", ["out_invoice", "out_refund"]],
                ["state", "=", "posted"],
                ["l10n_pl_ksef_status", "=", "draft"],
            ]);
            if (!ids.length) {
                this.notification.add(_t("Brak faktur do wysłania."), { type: "warning" });
                return;
            }
            const wizardResult = await this.orm.create("ksef.bulk.send.wizard", [
                { move_ids: [[6, 0, ids]] },
            ]);
            const wizardId = Array.isArray(wizardResult) ? wizardResult[0] : wizardResult;
            this.action.doAction({
                type: "ir.actions.act_window",
                name: _t("KSeF — Wyślij faktury"),
                res_model: "ksef.bulk.send.wizard",
                res_id: wizardId,
                view_mode: "form",
                views: [[false, "form"]],
                target: "new",
            });
        } finally {
            this.state.sending = false;
        }
    }

    async onSync() {
        if (this.state.syncing) return;
        this.state.syncing = true;
        try {
            await this.orm.call("ksef.vendor.buffer", "action_sync_from_ksef", []);
            this.notification.add(_t("Synchronizacja z KSeF zakończona."), { type: "success" });
            await this._loadAll();
        } catch (e) {
            const msg = (e.data && e.data.message) || e.message || String(e);
            this.notification.add(_t("Błąd synchronizacji: %s", msg), { type: "danger" });
        } finally {
            this.state.syncing = false;
        }
    }

    async onRefresh() {
        await this._loadAll();
        this.notification.add(_t("Dashboard odświeżony."), { type: "success" });
    }

    async onRetryRejected(moveId) {
        try {
            await this.orm.call("account.move", "action_retry_ksef", [[moveId]]);
            this.notification.add(_t("Faktura wysłana ponownie do KSeF."), { type: "success" });
            await this._loadAll();
        } catch (e) {
            const msg = (e.data && e.data.message) || e.message || String(e);
            this.notification.add(_t("Błąd ponownej wysyłki: %s", msg), { type: "danger" });
        }
    }

    // ────────────────────────────────────────────────────────────────────
    // Helpers (template)
    // ────────────────────────────────────────────────────────────────────

    statusLabel(status) {
        return ({
            draft: _t("Чернетка"),
            waiting: _t("Очікує"),
            accepted: _t("Прийнято"),
            rejected: _t("Відхилено"),
        })[status] || status;
    }

    statusBadgeClass(status) {
        return ({
            draft: "badge bg-secondary",
            waiting: "badge bg-warning text-dark",
            accepted: "badge bg-success",
            rejected: "badge bg-danger",
        })[status] || "badge bg-secondary";
    }

    formatAmount(amount, currency) {
        if (amount === undefined || amount === null) return "—";
        return Number(amount).toLocaleString(this._locale, {
            minimumFractionDigits: 2,
            maximumFractionDigits: 2,
        }) + " " + (currency || "PLN");
    }

    formatAmountCompact(amount) {
        if (!amount) return "0";
        if (amount >= 1_000_000) return (amount / 1_000_000).toFixed(1) + "M";
        if (amount >= 1_000) return (amount / 1_000).toFixed(1) + "k";
        return Math.round(amount).toString();
    }

    get hasInboxItems() {
        const i = this.state.inbox;
        return (
            i.rejectedItems.length > 0 ||
            i.waitingStuck > 0 ||
            i.draftOld > 0 ||
            i.bufferCount > 0
        );
    }
}

registry.category("actions").add("ksef_dashboard", KsefDashboard);
