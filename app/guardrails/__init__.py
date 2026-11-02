"""Guardrail layers: input (PII masking, attacks, off-topic), tool data and output.

A guardrail is a safety check around the LLM. The input layer checks what the customer sends,
the tool-data layer checks text that comes back from order data, and the output layer checks the
drafted answer before anyone reads it.
"""
