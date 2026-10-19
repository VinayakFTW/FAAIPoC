import json
import time
from datetime import datetime
import pandas as pd
import requests
import streamlit as st

# =====================================================================
# 1. SYNTHETIC ERP DATABASES (DUMMY DATA)
# =====================================================================

VENDOR_MASTER_DB = {
    "VEND-101": {"name": "TechNova Solutions", "status": "ACTIVE", "tax_id": "GST-27AAACT1234F1Z5", "tier": "Preferred"},
    "VEND-102": {"name": "Apex Industrial Supply", "status": "ACTIVE", "tax_id": "GST-27BBBCI5678G1Z2", "tier": "Standard"},
    "VEND-103": {"name": "CloudScale Systems", "status": "ACTIVE", "tax_id": "GST-27CCCCS9012H1Z9", "tier": "Standard"},
    "VEND-999": {"name": "ShadowLogistics LLC", "status": "SUSPENDED", "tax_id": "UNVERIFIED-TAX-000", "tier": "Unregistered"},
}

PURCHASE_ORDERS_DB = {
    "PO-2026-001": {
        "vendor_id": "VEND-101",
        "item": "Enterprise NVMe SSD 4TB",
        "ordered_qty": 20,
        "unit_price": 350.00,
        "tax_rate_pct": 18.0,
        "currency": "USD",
    },
    "PO-2026-002": {
        "vendor_id": "VEND-102",
        "item": "Ergonomic Mesh Task Chair",
        "ordered_qty": 50,
        "unit_price": 120.00,
        "tax_rate_pct": 18.0,
        "currency": "USD",
    },
    "PO-2026-003": {
        "vendor_id": "VEND-103",
        "item": "Dedicated Firewall Appliance",
        "ordered_qty": 10,
        "unit_price": 1000.00,
        "tax_rate_pct": 18.0,
        "currency": "USD",
    },
}

GOODS_RECEIPT_DB = {
    "PO-2026-001": {"grn_id": "GRN-8801", "received_qty": 20, "accepted_qty": 20, "warehouse_note": "Full shipment received in good condition."},
    "PO-2026-002": {"grn_id": "GRN-8802", "received_qty": 42, "accepted_qty": 42, "warehouse_note": "Short delivery: Only 42 of 50 chairs arrived on dock."},
    "PO-2026-003": {"grn_id": "GRN-8803", "received_qty": 10, "accepted_qty": 10, "warehouse_note": "All 10 appliances received."},
}

INCOMING_INVOICES = {
    "INV-9001 (Clean 3-Way Match)": {
        "invoice_id": "INV-9001",
        "po_reference": "PO-2026-001",
        "vendor_id": "VEND-101",
        "vendor_name": "TechNova Solutions",
        "invoice_date": "2026-10-15",
        "item": "Enterprise NVMe SSD 4TB",
        "billed_qty": 20,
        "billed_unit_price": 350.00,
        "billed_tax_rate_pct": 18.0,
        "scenario_desc": "All quantities, unit prices, and tax rates match PO and GRN exactly.",
    },
    "INV-9002 (Quantity Shortfall Discrepancy)": {
        "invoice_id": "INV-9002",
        "po_reference": "PO-2026-002",
        "vendor_id": "VEND-102",
        "vendor_name": "Apex Industrial Supply",
        "invoice_date": "2026-10-16",
        "item": "Ergonomic Mesh Task Chair",
        "billed_qty": 50,
        "billed_unit_price": 120.00,
        "billed_tax_rate_pct": 18.0,
        "scenario_desc": "Vendor billed for 50 chairs, but Warehouse GRN shows only 42 were delivered.",
    },
    "INV-9003 (Unit Price Overcharge > 5% Policy)": {
        "invoice_id": "INV-9003",
        "po_reference": "PO-2026-003",
        "vendor_id": "VEND-103",
        "vendor_name": "CloudScale Systems",
        "invoice_date": "2026-10-17",
        "item": "Dedicated Firewall Appliance",
        "billed_qty": 10,
        "billed_unit_price": 1180.00,  # 18% hike over $1000 PO price
        "billed_tax_rate_pct": 18.0,
        "scenario_desc": "Vendor billed $1,180/unit instead of contracted $1,000/unit (+18% variance exceeds 5% policy tolerance).",
    },
    "INV-9999 (Ghost Vendor / Missing PO Fraud)": {
        "invoice_id": "INV-9999",
        "po_reference": "PO-2026-999",
        "vendor_id": "VEND-999",
        "vendor_name": "ShadowLogistics LLC",
        "invoice_date": "2026-10-18",
        "item": "Consulting Retainer Services",
        "billed_qty": 1,
        "billed_unit_price": 8500.00,
        "billed_tax_rate_pct": 18.0,
        "scenario_desc": "Unregistered/Suspended vendor submitting an invoice with a non-existent PO number.",
    },
}

CORPORATE_POLICY = {
    "max_unit_price_variance_pct": 5.0,
    "allow_payment_above_grn_qty": False,
    "require_active_vendor": True,
    "high_value_threshold": 5000.0,
}

# =====================================================================
# 2. AGENT TOOLS (PYTHON FUNCTIONS)
# =====================================================================

def tool_verify_vendor(vendor_id: str) -> dict:
    return VENDOR_MASTER_DB.get(vendor_id, {"status": "NOT_FOUND", "name": "Unknown", "tax_id": "NONE", "tier": "None"})

def tool_fetch_po(po_ref: str) -> dict:
    return PURCHASE_ORDERS_DB.get(po_ref, None)

def tool_fetch_grn(po_ref: str) -> dict:
    return GOODS_RECEIPT_DB.get(po_ref, None)

def tool_calculate_variances(invoice: dict, po: dict, grn: dict) -> dict:
    billed_subtotal = invoice["billed_qty"] * invoice["billed_unit_price"]
    billed_tax = billed_subtotal * (invoice["billed_tax_rate_pct"] / 100.0)
    billed_total = billed_subtotal + billed_tax

    if not po or not grn:
        return {
            "po_found": bool(po),
            "grn_found": bool(grn),
            "billed_total": round(billed_total, 2),
            "approved_payable_total": 0.0,
            "overcharge_amount": round(billed_total, 2),
            "qty_variance": invoice["billed_qty"],
            "unit_price_variance_pct": 100.0,
            "tax_rate_variance": 0.0,
        }

    qty_variance = invoice["billed_qty"] - grn["accepted_qty"]
    price_diff = invoice["billed_unit_price"] - po["unit_price"]
    unit_price_variance_pct = (price_diff / po["unit_price"]) * 100.0
    tax_rate_variance = invoice["billed_tax_rate_pct"] - po["tax_rate_pct"]

    payable_qty = min(invoice["billed_qty"], grn["accepted_qty"])
    payable_unit_price = po["unit_price"] if unit_price_variance_pct > CORPORATE_POLICY["max_unit_price_variance_pct"] else invoice["billed_unit_price"]
    payable_subtotal = payable_qty * payable_unit_price
    payable_tax = payable_subtotal * (po["tax_rate_pct"] / 100.0)
    approved_payable_total = payable_subtotal + payable_tax

    return {
        "po_found": True,
        "grn_found": True,
        "billed_subtotal": round(billed_subtotal, 2),
        "billed_tax": round(billed_tax, 2),
        "billed_total": round(billed_total, 2),
        "approved_payable_total": round(approved_payable_total, 2),
        "overcharge_amount": round(billed_total - approved_payable_total, 2),
        "qty_variance": qty_variance,
        "unit_price_variance_pct": round(unit_price_variance_pct, 2),
        "tax_rate_variance": round(tax_rate_variance, 2),
        "payable_qty": payable_qty,
        "contract_unit_price": po["unit_price"],
    }

def query_ollama_optional(prompt: str, model_name: str = "beam:cloud") -> str:
    """Optional helper to query local Ollama instance if enabled by user."""
    try:
        response = requests.post(
            "http://localhost:11434/api/generate",
            json={"model": model_name, "prompt": prompt, "stream": False},
            timeout=6,
        )
        if response.status_code == 200:
            return response.json().get("response", "").strip()
    except Exception:
        pass
    return ""

# =====================================================================
# 3. MULTI-AGENT DEFINITIONS & ORCHESTRATION
# =====================================================================

def run_planner_agent(invoice: dict, use_ollama: bool, ollama_model: str) -> dict:
    total_est = invoice["billed_qty"] * invoice["billed_unit_price"] * (1 + invoice["billed_tax_rate_pct"] / 100)
    priority = "HIGH (Senior Audit Required)" if total_est >= CORPORATE_POLICY["high_value_threshold"] else "STANDARD"
    tasks = [
        f"TASK-1: Validate vendor '{invoice['vendor_name']}' ({invoice['vendor_id']}) in Vendor Master Registry.",
        f"TASK-2: Retrieve Purchase Order '{invoice['po_reference']}' for contracted price and tax terms.",
        f"TASK-3: Cross-check Warehouse Goods Receipt Note (GRN) for physical quantity accepted.",
        "TASK-4: Execute 3-way mathematical variance analysis (Qty, Unit Price %, Tax).",
        "TASK-5: Run Critic Agent compliance verification before triggering ERP automation.",
    ]
    llm_note = ""
    if use_ollama:
        llm_note = query_ollama_optional(
            f"In one concise sentence, state the audit objective for invoice {invoice['invoice_id']} worth ${total_est:.2f}.",
            ollama_model,
        )
    return {"priority": priority, "estimated_gross": round(total_est, 2), "tasks": tasks, "llm_note": llm_note}


def run_research_agent(invoice: dict) -> dict:
    vendor_info = tool_verify_vendor(invoice["vendor_id"])
    po_info = tool_fetch_po(invoice["po_reference"])
    grn_info = tool_fetch_grn(invoice["po_reference"])
    return {"vendor_info": vendor_info, "po_info": po_info, "grn_info": grn_info}


def run_executor_agent(invoice: dict, research_data: dict, simulate_lenient_bug: bool = False) -> dict:
    variances = tool_calculate_variances(invoice, research_data["po_info"], research_data["grn_info"])
    vendor_status = research_data["vendor_info"]["status"]

    reasons = []
    if vendor_status != "ACTIVE" or not variances["po_found"]:
        verdict = "REJECTED_COMPLIANCE_OR_FRAUD"
        reasons.append("Vendor is not ACTIVE or Purchase Order does not exist in ERP.")
    elif simulate_lenient_bug:
        # Intentionally lenient output so the user can watch the Critic Agent catch and override it
        verdict = "AUTO_APPROVED"
        reasons.append("Executor lenient mode: Approved invoice despite variance (awaiting Critic check).")
    else:
        if variances["unit_price_variance_pct"] > CORPORATE_POLICY["max_unit_price_variance_pct"]:
            verdict = "ON_HOLD_PRICE_DISPUTE"
            reasons.append(
                f"Unit price variance (+{variances['unit_price_variance_pct']}%) exceeds corporate limit of {CORPORATE_POLICY['max_unit_price_variance_pct']}%."
            )
        elif variances["qty_variance"] > 0:
            verdict = "PARTIAL_APPROVAL_QTY_SHORTFALL"
            reasons.append(
                f"Billed quantity ({invoice['billed_qty']}) exceeds warehouse accepted GRN quantity ({variances['payable_qty']})."
            )
        else:
            verdict = "AUTO_APPROVED"
            reasons.append("100% 3-Way Match verified across Invoice, PO, and Warehouse GRN.")

    return {"preliminary_verdict": verdict, "variances": variances, "reasons": reasons}


def run_critic_agent(invoice: dict, research_data: dict, executor_output: dict) -> dict:
    v = executor_output["variances"]
    prelim = executor_output["preliminary_verdict"]
    vendor_status = research_data["vendor_info"]["status"]

    issues_detected = []
    expected_verdict = "AUTO_APPROVED"

    if vendor_status != "ACTIVE" or not v["po_found"] or not v["grn_found"]:
        expected_verdict = "REJECTED_COMPLIANCE_OR_FRAUD"
        if prelim != expected_verdict:
            issues_detected.append("CRITICAL: Executor failed to block an inactive vendor or missing PO.")
    elif v["unit_price_variance_pct"] > CORPORATE_POLICY["max_unit_price_variance_pct"]:
        expected_verdict = "ON_HOLD_PRICE_DISPUTE"
        if prelim != expected_verdict:
            issues_detected.append(
                f"POLICY VIOLATION CAUGHT: Unit price hike of {v['unit_price_variance_pct']}% violates the 5.0% ceiling."
            )
    elif v["qty_variance"] > 0:
        expected_verdict = "PARTIAL_APPROVAL_QTY_SHORTFALL"
        if prelim != expected_verdict:
            issues_detected.append(
                f"QUANTITY MISMATCH CAUGHT: Cannot auto-approve {invoice['billed_qty']} units when GRN only accepted {v['payable_qty']}."
            )

    # Verify arithmetic integrity
    recalc_gross = round(
        invoice["billed_qty"] * invoice["billed_unit_price"] * (1 + invoice["billed_tax_rate_pct"] / 100.0), 2
    )
    math_verified = recalc_gross == v["billed_total"]

    if issues_detected:
        critic_status = "OVERRIDDEN_BY_CRITIC"
        final_verdict = expected_verdict
        audit_note = "Critic Agent intercepted an invalid Executor decision and enforced corporate policy."
    else:
        critic_status = "VALIDATED_100_PERCENT"
        final_verdict = prelim
        audit_note = "Critic Agent verified all arithmetic and confirmed full policy alignment."

    return {
        "critic_status": critic_status,
        "math_verified": math_verified,
        "issues_detected": issues_detected,
        "final_verdict": final_verdict,
        "audit_note": audit_note,
    }


def run_business_automation_agent(invoice: dict, research_data: dict, executor_output: dict, critic_output: dict) -> dict:
    verdict = critic_output["final_verdict"]
    v = executor_output["variances"]

    erp_payload = {
        "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "invoice_id": invoice["invoice_id"],
        "po_reference": invoice["po_reference"],
        "vendor_id": invoice["vendor_id"],
        "final_status": verdict,
        "billed_amount_usd": v["billed_total"],
        "approved_payout_usd": v["approved_payable_total"] if verdict in ["AUTO_APPROVED", "PARTIAL_APPROVAL_QTY_SHORTFALL"] else 0.0,
        "savings_or_blocked_usd": v["overcharge_amount"] if verdict != "REJECTED_COMPLIANCE_OR_FRAUD" else v["billed_total"],
        "critic_signoff": critic_output["critic_status"],
    }

    if verdict == "AUTO_APPROVED":
        email_subject = f"[PAYMENT SCHEDULED] Invoice {invoice['invoice_id']} Approved for Full Settlement"
        email_body = (
            f"Dear {invoice['vendor_name']} Finance Team,\n\n"
            f"Your invoice {invoice['invoice_id']} referencing {invoice['po_reference']} has passed 3-way reconciliation.\n"
            f"- Approved Amount: ${v['approved_payable_total']:,.2f}\n"
            f"- Scheduled Payment Run: Next ACH Batch (Net-30)\n\n"
            f"Regards,\nAutomated AP Operations (FinOps Sentinel)"
        )
    elif verdict == "PARTIAL_APPROVAL_QTY_SHORTFALL":
        email_subject = f"[DISPUTE NOTICE - QTY SHORTFALL] Partial Settlement for Invoice {invoice['invoice_id']}"
        email_body = (
            f"Dear {invoice['vendor_name']} Billing Team,\n\n"
            f"During automated 3-way matching for Invoice {invoice['invoice_id']} ({invoice['po_reference']}), "
            f"our warehouse Goods Receipt Note ({research_data['grn_info']['grn_id']}) confirmed delivery of only "
            f"{v['payable_qty']} units out of {invoice['billed_qty']} billed.\n\n"
            f"- Billed Total: ${v['billed_total']:,.2f}\n"
            f"- Approved Partial Payout (for {v['payable_qty']} units): ${v['approved_payable_total']:,.2f}\n"
            f"- Disputed Hold Amount: ${v['overcharge_amount']:,.2f}\n\n"
            f"Please issue a credit note for the {v['qty_variance']} undelivered units or provide proof of delivery.\n\n"
            f"Regards,\nAutomated AP Operations (FinOps Sentinel)"
        )
    elif verdict == "ON_HOLD_PRICE_DISPUTE":
        email_subject = f"[PAYMENT HOLD - PRICE VARIANCE] Invoice {invoice['invoice_id']} Exceeds Contract Rate"
        email_body = (
            f"Dear {invoice['vendor_name']} Billing Team,\n\n"
            f"Invoice {invoice['invoice_id']} has been placed on hold due to a unit price discrepancy against {invoice['po_reference']}.\n\n"
            f"- Contracted PO Unit Price: ${v['contract_unit_price']:,.2f}\n"
            f"- Billed Unit Price: ${invoice['billed_unit_price']:,.2f} (+{v['unit_price_variance_pct']}% variance)\n"
            f"- Corporate Tolerance Limit: {CORPORATE_POLICY['max_unit_price_variance_pct']}%\n"
            f"- Total Overcharge Flagged: ${v['overcharge_amount']:,.2f}\n\n"
            f"Please submit a revised invoice at the contracted rate of ${v['contract_unit_price']:,.2f}/unit.\n\n"
            f"Regards,\nAutomated AP Operations (FinOps Sentinel)"
        )
    else:
        email_subject = f"[SECURITY ALERT - REJECTED] Unverified Invoice {invoice['invoice_id']} Blocked"
        email_body = (
            f"INTERNAL AP COMPLIANCE ALERT:\n\n"
            f"Invoice {invoice['invoice_id']} from '{invoice['vendor_name']}' ({invoice['vendor_id']}) was automatically blocked.\n"
            f"- Vendor Status: {research_data['vendor_info']['status']}\n"
            f"- PO Found in ERP: {v['po_found']}\n"
            f"- Blocked Exposure: ${v['billed_total']:,.2f}\n\n"
            f"Escalated to Internal Audit & Fraud Prevention."
        )

    return {"erp_payload": erp_payload, "email_subject": email_subject, "email_body": email_body}


# =====================================================================
# 4. STREAMLIT USER INTERFACE
# =====================================================================

st.set_page_config(page_title="FinOps Sentinel | Agentic 3-Way Match PoC", layout="wide")

st.title("🛡️ FinOps Sentinel: Multi-Agent 3-Way Invoice Reconciliation")
st.caption("Domain: Business Operations & Finance | Architecture: Planner → Research → Executor → Critic → Business Automation")

with st.sidebar:
    st.header("⚙️ Agentic Control Panel")
    selected_scenario = st.selectbox("Select Test Invoice Scenario:", list(INCOMING_INVOICES.keys()))
    invoice_data = INCOMING_INVOICES[selected_scenario]

    st.divider()
    st.subheader("🧪 Critic Self-Correction Test")
    simulate_bug = st.checkbox(
        "Simulate Flawed Executor Decision",
        value=False,
        help="Forces the Executor Agent to rubber-stamp the invoice so you can watch the Critic Agent catch and override the mistake.",
    )

    st.divider()
    st.subheader("🤖 Model Engine")
    use_ollama = st.toggle("Enable Local Ollama LLM Reasoning", value=False)
    ollama_model = st.text_input("Ollama Model Tag:", value="llama3.2", disabled=not use_ollama)

    st.divider()
    st.markdown("**Corporate Policy Rules:**")
    st.json(CORPORATE_POLICY)

# Display Selected Invoice & ERP Reference Data
col1, col2 = st.columns([1, 1])

with col1:
    st.subheader("📄 Incoming Vendor Invoice")
    st.info(f"**Scenario:** {invoice_data['scenario_desc']}")
    st.dataframe(pd.DataFrame([invoice_data]).drop(columns=["scenario_desc"]), use_container_width=True)

with col2:
    st.subheader("🗄️ Live ERP Reference Preview (PO & GRN)")
    po_preview = PURCHASE_ORDERS_DB.get(invoice_data["po_reference"], {"Note": "No Matching PO in ERP"})
    grn_preview = GOODS_RECEIPT_DB.get(invoice_data["po_reference"], {"Note": "No Matching GRN in ERP"})
    st.write("**Purchase Order Record:**", po_preview)
    st.write("**Warehouse GRN Record:**", grn_preview)

st.divider()

if st.button("🚀 Run Multi-Agent Reconciliation Workflow", type="primary", use_container_width=True):
    with st.status("Executing Multi-Agent Orchestration Pipeline...", expanded=True) as status:
        # Step 1: Planner
        st.write("🧠 **[1/5] Planner Agent:** Decomposing invoice audit into verification tasks...")
        time.sleep(0.35)
        plan_res = run_planner_agent(invoice_data, use_ollama, ollama_model)

        # Step 2: Research
        st.write("🔍 **[2/5] Research Agent:** Querying Vendor Master, Purchase Order, and Warehouse GRN tables...")
        time.sleep(0.35)
        research_res = run_research_agent(invoice_data)

        # Step 3: Executor
        st.write("⚙️ **[3/5] Executor Agent:** Running Python 3-way variance calculation tools...")
        time.sleep(0.35)
        exec_res = run_executor_agent(invoice_data, research_res, simulate_lenient_bug=simulate_bug)

        # Step 4: Critic
        st.write("⚖️ **[4/5] Critic Agent:** Auditing Executor math and enforcing corporate compliance policy...")
        time.sleep(0.35)
        critic_res = run_critic_agent(invoice_data, research_res, exec_res)

        # Step 5: Business Automation
        st.write("📨 **[5/5] Business Automation Agent:** Generating ERP ledger payload and vendor communication...")
        time.sleep(0.35)
        auto_res = run_business_automation_agent(invoice_data, research_res, exec_res, critic_res)

        status.update(label="Multi-Agent Workflow Completed!", state="complete", expanded=False)

    # Top KPI Metrics
    v = exec_res["variances"]
    m1, m2, m3, m4 = st.columns(4)
    m1.metric("Billed Invoice Total", f"${v['billed_total']:,.2f}")
    m2.metric("Approved Payout", f"${auto_res['erp_payload']['approved_payout_usd']:,.2f}")
    m3.metric("Overcharge / Fraud Blocked", f"${auto_res['erp_payload']['savings_or_blocked_usd']:,.2f}")
    m4.metric("Final Agentic Verdict", critic_res["final_verdict"])

    # Detailed Agent Tabs
    t1, t2, t3, t4, t5 = st.tabs([
        "1️⃣ Planner Agent",
        "2️⃣ Research Agent",
        "3️⃣ Executor Agent",
        "4️⃣ Critic Agent (Validation)",
        "5️⃣ Business Automation",
    ])

    with t1:
        st.markdown(f"**Audit Priority Tier:** `{plan_res['priority']}` | **Estimated Gross Exposure:** `${plan_res['estimated_gross']:,.2f}`")
        if plan_res["llm_note"]:
            st.success(f"**Ollama LLM Summary:** {plan_res['llm_note']}")
        for task in plan_res["tasks"]:
            st.markdown(f"- ✅ {task}")

    with t2:
        c_a, c_b, c_c = st.columns(3)
        c_a.markdown("**Vendor Registry Lookup**")
        c_a.json(research_res["vendor_info"])
        c_b.markdown("**Purchase Order Lookup**")
        c_b.json(research_res["po_info"] if research_res["po_info"] else {"error": "PO_NOT_FOUND"})
        c_c.markdown("**Warehouse GRN Lookup**")
        c_c.json(research_res["grn_info"] if research_res["grn_info"] else {"error": "GRN_NOT_FOUND"})

    with t3:
        st.markdown(f"**Executor Preliminary Verdict:** `{exec_res['preliminary_verdict']}`")
        for r in exec_res["reasons"]:
            st.write(f"- {r}")
        st.markdown("**Deterministic Tool Math Output:**")
        st.json(exec_res["variances"])

    with t4:
        if critic_res["critic_status"] == "OVERRIDDEN_BY_CRITIC":
            st.error(f"🚨 **Critic Status:** {critic_res['critic_status']}")
            for issue in critic_res["issues_detected"]:
                st.warning(issue)
        else:
            st.success(f"✅ **Critic Status:** {critic_res['critic_status']}")
        st.write(f"**Arithmetic Integrity Verified:** `{critic_res['math_verified']}`")
        st.write(f"**Critic Note:** {critic_res['audit_note']}")
        st.write(f"**Final Enforced Verdict:** `{critic_res['final_verdict']}`")

    with t5:
        col_erp, col_mail = st.columns([1, 1])
        with col_erp:
            st.markdown("**ERP Ledger API Payload**")
            st.json(auto_res["erp_payload"])
            st.download_button(
                "📥 Download Audit Report (JSON)",
                data=json.dumps(auto_res["erp_payload"], indent=2),
                file_name=f"audit_{invoice_data['invoice_id']}.json",
                mime="application/json",
            )
        with col_mail:
            st.markdown("**Automated Vendor / Compliance Email Draft**")
            st.text_input("Subject", value=auto_res["email_subject"], disabled=True)
            st.text_area("Body", value=auto_res["email_body"], height=220, disabled=True)