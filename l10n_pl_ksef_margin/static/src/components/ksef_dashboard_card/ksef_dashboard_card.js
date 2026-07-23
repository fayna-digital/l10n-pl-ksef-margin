/** @odoo-module **/

import { Component, onWillStart, useState } from "@odoo/owl";
import { useService } from "@web/core/utils/hooks";

/**
 * KsefAlertBanner — injected into the accounting dashboard above journal cards.
 * Shows only when there are KSeF issues that need attention (rejected, overdue, etc.).
 * Silent when everything is OK.
 */
export class KsefAlertBanner extends Component {
    static template = "l10n_pl_ksef_margin.KsefAlertBanner";
    static props = {};

    setup() {
        this.orm = useService("orm");
        this.actionService = useService("action");
        this.state = useState({ rejected: 0, draft: 0, waiting: 0, loaded: false });
        onWillStart(() => this._load());
    }

    async _load() {
        const baseDomain = [
            ["move_type", "in", ["out_invoice", "out_refund"]],
            ["state", "=", "posted"],
        ];
        const groups = await this.orm.readGroup(
            "account.move", baseDomain,
            ["l10n_pl_ksef_status"], ["l10n_pl_ksef_status"]
        );
        const counts = { rejected: 0, draft: 0, waiting: 0 };
        for (const g of groups) {
            const s = g.l10n_pl_ksef_status;
            if (s in counts) counts[s] = g.l10n_pl_ksef_status_count;
        }
        Object.assign(this.state, { ...counts, loaded: true });
    }

    get shouldShow() {
        return this.state.loaded && (
            this.state.rejected > 0 ||
            this.state.draft > 30 ||
            this.state.waiting > 0
        );
    }

    get alertVariant() {
        if (this.state.rejected > 0) return "danger";
        if (this.state.draft > 30) return "warning";
        return "info";
    }

    get alertIcon() {
        return this.alertVariant === "danger" ? "times-circle" : "exclamation-triangle";
    }

    get alertMessage() {
        const parts = [];
        if (this.state.rejected > 0)
            parts.push(`${this.state.rejected} відхилено`);
        if (this.state.waiting > 0)
            parts.push(`${this.state.waiting} очікують відповіді`);
        if (this.state.draft > 30)
            parts.push(`${this.state.draft} не надіслано`);
        return parts.join(" · ");
    }

    openDashboard() {
        this.actionService.doAction("l10n_pl_ksef_margin.action_ksef_dashboard");
    }
}
