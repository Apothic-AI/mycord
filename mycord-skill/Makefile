.PHONY: help check

help:  ## Show this help message
	@echo 'Usage: make [target]'
	@echo ''
	@awk 'BEGIN {FS = ":.*?## "} /^[a-zA-Z_-]+:.*?## / {printf "  %-8s %s\n", $$1, $$2}' $(MAKEFILE_LIST)

check:  ## Verify every component the shipped table names actually exists
	@cd .. && missing=0; \
	for c in mycord-discord-skill mycord-telegram-skill mycord-slack-skill \
	         google-voice-skill mycord-irc-skill mycord-email-skill \
	         ferdium-skill gvoice; do \
	  if [ -d "$$c" ]; then echo "ok      $$c"; else echo "MISSING $$c"; missing=1; fi; \
	done; \
	exit $$missing
