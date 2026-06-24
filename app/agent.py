# ruff: noqa
import datetime
import os
import sys
import re
import json
from pydantic import BaseModel, Field
from typing import Any

from google.adk.agents import LlmAgent
from google.adk.workflow import Workflow, START, node
from google.adk.agents.context import Context
from google.adk.events.event import Event
from google.adk.events.request_input import RequestInput
from google.adk.apps import App, ResumabilityConfig
from google.adk.tools import AgentTool
from google.adk.tools.mcp_tool import McpToolset
from google.adk.tools.mcp_tool.mcp_session_manager import StdioConnectionParams
from mcp import StdioServerParameters
from google.genai import types

from .config import config

# Define MCP server toolset connection
# We use sys.executable to run Python in the same virtual environment
mcp_toolset = McpToolset(
    connection_params=StdioConnectionParams(
        server_params=StdioServerParameters(
            command=sys.executable,
            args=[os.path.join(os.path.dirname(__file__), "mcp_server.py")],
        )
    )
)

# Pydantic schemas for structured agent outputs
class MedicationResponse(BaseModel):
    message: str = Field(description="A message summarizing the medication tracking action.")

class VisitResponse(BaseModel):
    message: str = Field(description="A message summarizing the doctor visit coordination action.")
    needs_confirmation: bool = Field(description="True if human confirmation is required for appointment booking/changes.")
    proposed_doctor: str = Field(default="", description="Name of the doctor if scheduling.")
    proposed_time: str = Field(default="", description="Proposed date and time for the appointment.")

class OrchestratorResponse(BaseModel):
    response_text: str = Field(description="The main text response to the user.")
    needs_confirmation: bool = Field(default=False, description="True if a doctor visit or action needs human confirmation.")
    proposed_doctor: str = Field(default="", description="Name of the doctor to confirm.")
    proposed_time: str = Field(default="", description="Proposed date and time to confirm.")

# Specialized Sub-Agent 1: Medication Manager
medication_manager = LlmAgent(
    name="medication_manager",
    model=config.model,
    instruction="""You are the Medication Manager Agent.
You help track medication schedules, list active medications, log when medication was taken, and add new medication schedules.
Use the MCP tools provided to read, add, and update medication records.
Always summarize clearly for an elderly user or their caregiver.""",
    description="Helps manage and track medication schedules and logging.",
    tools=[mcp_toolset],
    output_schema=MedicationResponse,
)

# Specialized Sub-Agent 2: Doctor Visit Coordinator
visit_coordinator = LlmAgent(
    name="visit_coordinator",
    model=config.model,
    instruction="""You are the Doctor Visit Coordinator Agent.
You coordinate doctor appointments, track upcoming visits, and schedule new appointments.
Use the MCP tools to query or schedule appointments.
If the user wants to schedule or reschedule a doctor visit, you must set needs_confirmation=True in your output and specify the proposed details so the system can ask the user for confirmation.
If they are just asking for a list of upcoming appointments, set needs_confirmation=False.""",
    description="Helps schedule and coordinate doctor visits and appointments.",
    tools=[mcp_toolset],
    output_schema=VisitResponse,
)

# Orchestrator Agent
orchestrator = LlmAgent(
    name="orchestrator",
    model=config.model,
    instruction="""You are the Elder Care Orchestrator Agent.
Determine if the user is asking about medication schedules (e.g., listing medications, logging a dose, adding a schedule) or doctor visits (e.g., checking appointments, scheduling a new visit).
If medication-related, call the medication_manager tool.
If visit-related, call the visit_coordinator tool.
For any general questions or greeting, answer directly without using specialist tools.
Keep your response friendly, clear, and reassuring, suitable for an elderly user or their caregiver.
If a sub-agent indicates that confirmation is needed, propagate needs_confirmation, proposed_doctor, and proposed_time in your output.""",
    tools=[AgentTool(medication_manager), AgentTool(visit_coordinator)],
    output_schema=OrchestratorResponse,
)

# Workflow Function Nodes

def security_checkpoint(ctx: Context, node_input: Any) -> Event:
    """Validates user inputs for security concerns before orchestrating."""
    if hasattr(node_input, "parts") and node_input.parts:
        text = node_input.parts[0].text
    else:
        text = str(node_input)

    # 1. PII Scrubbing (Regex)
    ssn_regex = r"\b\d{3}-\d{2}-\d{4}\b"
    phone_regex = r"\b\d{3}-\d{3}-\d{4}\b"
    scrubbed_text = re.sub(ssn_regex, "[SSN REDACTED]", text)
    scrubbed_text = re.sub(phone_regex, "[PHONE REDACTED]", scrubbed_text)

    # 2. Prompt Injection Detection
    injection_keywords = ["ignore previous instructions", "system prompt", "override instructions", "you are now a"]
    is_injection = any(keyword in text.lower() for keyword in injection_keywords)

    # 3. Domain-Specific Rule: Consent check for sharing medical data
    has_unsafe_sharing = "share" in text.lower() and "insurance" in text.lower() and "consent" not in text.lower()

    # 4. Structured JSON Audit Log
    severity = "INFO"
    if is_injection:
        severity = "CRITICAL"
    elif has_unsafe_sharing:
        severity = "WARNING"

    audit_log = {
        "timestamp": datetime.datetime.now().isoformat(),
        "severity": severity,
        "input_length": len(text),
        "pii_redacted": scrubbed_text != text,
        "injection_detected": is_injection,
        "unsafe_sharing_detected": has_unsafe_sharing
    }
    print(f"[AUDIT LOG] {json.dumps(audit_log)}")

    if is_injection or has_unsafe_sharing:
        ctx.state["security_error"] = "Security validation failed. Potential prompt injection or unauthorized data sharing detected."
        return Event(output="Security validation failed.", route="unsafe")

    return Event(output=scrubbed_text, route="safe")


@node(rerun_on_resume=True)
async def orchestrator_node(ctx: Context, node_input: Any) -> Event:
    """Executes the orchestrator agent and manages routing/state based on its output."""
    if ctx.resume_inputs and "confirm_action" in ctx.resume_inputs:
        user_confirmed = ctx.resume_inputs["confirm_action"]
        pending = ctx.state.get("pending_visit", {})
        doctor = pending.get("doctor")
        time = pending.get("time")

        if str(user_confirmed).lower() in ["yes", "y", "confirm", "approve"]:
            user_message = f"User has CONFIRMED the appointment booking with {doctor} at {time}. Please book it now."
        else:
            user_message = f"User has DECLINED the appointment booking with {doctor} at {time}. Please notify the user."
        
        ctx.state["pending_visit"] = None
    else:
        if hasattr(node_input, "parts") and node_input.parts:
            user_message = node_input.parts[0].text
        else:
            user_message = str(node_input)

    # Run the orchestrator LlmAgent
    result = await ctx.run_node(orchestrator, node_input=user_message)
    
    # Process structured OrchestratorResponse
    needs_conf = result.get("needs_confirmation", False)
    response_text = result.get("response_text", "")

    if needs_conf:
        ctx.state["pending_visit"] = {
            "doctor": result.get("proposed_doctor"),
            "time": result.get("proposed_time")
        }
        return Event(output=response_text, route="needs_confirmation")

    return Event(output=response_text, route="done")


async def human_verification_node(ctx: Context, node_input: Any):
    """Pauses the workflow to request human confirmation for bookings."""
    pending = ctx.state.get("pending_visit", {})
    doctor = pending.get("doctor", "Unknown Doctor")
    time = pending.get("time", "Unknown Time")
    message = f"Please confirm if you want to book the appointment with {doctor} at {time}. (Yes/No)"
    
    yield RequestInput(interrupt_id="confirm_action", message=message)


def security_violation_node(ctx: Context, node_input: Any):
    """Outputs the security error message to the user."""
    error_msg = ctx.state.get("security_error", "Access Denied due to security policy.")
    yield Event(content=types.Content(role='model', parts=[types.Part.from_text(text=error_msg)]))
    yield Event(output=error_msg)


def final_output_node(ctx: Context, node_input: Any):
    """Emits the final agent response to the user interface."""
    yield Event(content=types.Content(role='model', parts=[types.Part.from_text(text=str(node_input))]))
    yield Event(output=str(node_input))


# ADK 2.0 Workflow Definition
workflow_agent = Workflow(
    name="elder_care_workflow",
    edges=[
        (START, security_checkpoint),
        (security_checkpoint, {"safe": orchestrator_node, "unsafe": security_violation_node}),
        (orchestrator_node, {"needs_confirmation": human_verification_node, "done": final_output_node}),
        (human_verification_node, orchestrator_node),
        (security_violation_node, final_output_node),
    ],
    description="Elder Care Assistant Workflow that coordinates doctor visits and medication schedules securely.",
)

# App instance
app = App(
    root_agent=workflow_agent,
    name="app",
    resumability_config=ResumabilityConfig(is_resumable=True),
)

# Alias for compatibility with scaffolded integration tests
root_agent = workflow_agent

