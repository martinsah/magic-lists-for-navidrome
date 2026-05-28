import os
import asyncio
import httpx
import json
import re
from typing import Optional, Dict, Any, Union, NoReturn
from dataclasses import dataclass

@dataclass
class ProviderConfig:
    """Configuration for an AI provider"""
    base_url: str
    requires_key: bool
    default_model: str
    signup_url: str

class AIProviderConfig:
    """Hardcoded configurations for all supported AI providers"""
    
    PROVIDERS: Dict[str, ProviderConfig] = {
        "openrouter": ProviderConfig(
            base_url="https://openrouter.ai/api/v1/chat/completions",
            requires_key=True,
            default_model="openai/gpt-3.5-turbo",
            signup_url="https://openrouter.ai/"
        ),
        "groq": ProviderConfig(
            base_url="https://api.groq.com/openai/v1/chat/completions", 
            requires_key=True,
            default_model="mixtral-8x7b-32768",
            signup_url="https://console.groq.com/"
        ),
        "google": ProviderConfig(
            base_url="https://generativelanguage.googleapis.com/v1beta",
            requires_key=True,
            default_model="gemini-2.5-flash",
            signup_url="https://ai.google.dev/"
        ),
        "ollama": ProviderConfig(
            base_url="http://localhost:11434/v1/chat/completions",
            requires_key=False,
            default_model="llama3.2",
            signup_url=""  # Not applicable for local models
        )
    }

class AIProvider:
    """AI provider abstraction for OpenRouter, Groq, and Ollama"""
    
    def __init__(self, provider_type: str, api_key: Optional[str], model: str, base_url: str):
        self.provider_type = provider_type
        self.api_key = api_key
        self.model = model
        self.base_url = base_url
        self.client = httpx.AsyncClient()
    
    async def generate(
        self,
        system_prompt: str,
        user_prompt: str,
        max_tokens: int = 16000,
        temperature: float = 0.7,
        json_response: bool = False,
        include_suggestions: bool = False,
        response_schema: Optional[Dict[str, Any]] = None,
    ) -> str:
        """Send chat completion request to configured AI provider"""
        
        # Handle Google AI's different API format
        if self.provider_type == "google":
            return await self._generate_google(
                system_prompt,
                user_prompt,
                max_tokens,
                temperature,
                json_response=json_response,
                include_suggestions=include_suggestions,
                response_schema=response_schema,
            )
        
        # Build headers - only include Authorization for providers that require keys
        headers = {"Content-Type": "application/json"}
        if self.provider_type in ["openrouter", "groq"] and self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        
        # Build payload - all providers use OpenAI-compatible format
        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt}
            ],
            "max_tokens": max_tokens,
            "temperature": temperature
        }
        
        # Set timeout based on provider type
        if self.provider_type == "ollama":
            # Allow user to override Ollama timeout (default 180 seconds)
            timeout = float(os.getenv("OLLAMA_TIMEOUT", "180"))
            max_retries = 3
            retry_delay = 10
            
            # Handle Ollama model loading with retry logic
            for attempt in range(max_retries):
                try:
                    response = await self.client.post(
                        self.base_url,
                        json=payload,
                        headers=headers,
                        timeout=timeout
                    )
                    response.raise_for_status()
                    
                    result = response.json()
                    return result["choices"][0]["message"]["content"].strip()
                    
                except httpx.HTTPStatusError as e:
                    if e.response.status_code == 500:
                        # Check if it's a model loading error
                        try:
                            error_data = e.response.json()
                            error_message = error_data.get("error", {}).get("message", "")
                            
                            if "loading model" in error_message.lower():
                                print(f"🔄 Model still loading (attempt {attempt + 1}/{max_retries}), waiting {retry_delay}s...")
                                if attempt < max_retries - 1:
                                    import asyncio
                                    await asyncio.sleep(retry_delay)
                                    retry_delay += 10
                                    continue
                                else:
                                    print(f"❌ Model loading timeout after {max_retries} attempts")
                                    raise Exception(f"Ollama model '{self.model}' is still loading after {max_retries * retry_delay}s. Try again in a few minutes.")
                            else:
                                raise
                        except (json.JSONDecodeError, KeyError):
                            raise
                    else:
                        raise
                except Exception as e:
                    if attempt == max_retries - 1:
                        raise
                    else:
                        print(f"🔄 Request failed (attempt {attempt + 1}/{max_retries}): {str(e)}")
                        import asyncio
                        await asyncio.sleep(retry_delay)
                        continue
        else:
            # OpenRouter and Groq - single attempt with standard timeout
            timeout = 30.0
            response = await self.client.post(
                self.base_url,
                json=payload,
                headers=headers,
                timeout=timeout
            )
            response.raise_for_status()
            
            result = response.json()
            return result["choices"][0]["message"]["content"].strip()
    
    async def _generate_google(
        self,
        system_prompt: str,
        user_prompt: str,
        max_tokens: int = 16000,
        temperature: float = 0.7,
        json_response: bool = False,
        include_suggestions: bool = False,
        response_schema: Optional[Dict[str, Any]] = None,
    ) -> Union[str, NoReturn]:  # type: ignore
        """Handle Google AI's specific API format with controlled generation for JSON"""
        url = f"{self.base_url}/models/{self.model}:generateContent?key={self.api_key}"

        if json_response:
            combined_prompt = f"{system_prompt}\n\n{user_prompt}"
        else:
            combined_prompt = f"""
        {system_prompt}

        Important: Your response must be formatted as a valid JSON object.
        Do not include any explanatory text outside the JSON structure.
        Return only the JSON object, nothing else.

        {user_prompt}
        """

        # Genre mix: scale output budget with requested playlist size
        is_genre_mix = "genre_mix" in user_prompt.lower() or "Select exactly" in user_prompt
        if is_genre_mix:
            size_match = re.search(r"Select (?:exactly )?(\d+) tracks", user_prompt)
            requested = int(size_match.group(1)) if size_match else 25
            max_output = min(int(max_tokens), 16384 if requested > 50 else 8192)
        else:
            max_output = min(int(max_tokens), 16000)

        generation_config: Dict[str, Any] = {
            "temperature": temperature,
            "maxOutputTokens": max_output,
        }

        # Gemini 2.5 Flash defaults to dynamic thinking, which consumes most of
        # maxOutputTokens and truncates structured JSON (track_ids + suggestions).
        if json_response and "2.5" in (self.model or ""):
            generation_config["thinkingConfig"] = {"thinkingBudget": 0}
            if include_suggestions:
                generation_config["maxOutputTokens"] = max(max_output, 4096)

        if json_response:
            generation_config["responseMimeType"] = "application/json"
            if response_schema is not None:
                generation_config["responseSchema"] = response_schema
            else:
                schema_properties: Dict[str, Any] = {
                    "track_ids": {
                        "type": "ARRAY",
                        "items": {"type": "INTEGER"},
                    },
                    "reasoning": {"type": "STRING"},
                }
                if include_suggestions:
                    schema_properties["suggested_tracks"] = {
                        "type": "ARRAY",
                        "items": {
                            "type": "OBJECT",
                            "properties": {
                                "title": {"type": "STRING"},
                                "artist": {"type": "STRING"},
                                "album": {"type": "STRING"},
                                "note": {"type": "STRING"},
                            },
                            "required": ["title", "artist"],
                        },
                    }
                generation_config["responseSchema"] = {
                    "type": "OBJECT",
                    "properties": schema_properties,
                    "required": ["track_ids", "reasoning"],
                }

        payload = {
            "contents": [{
                "parts": [{
                    "text": combined_prompt
                }]
            }],
            "generationConfig": generation_config
        }

        headers = {"Content-Type": "application/json"}

        # Optional debug: save payload to file (never block curation on failure)
        if os.getenv("DEBUG_SAVE_AI_PAYLOADS", "").lower() in ("1", "true", "yes"):
            import time
            timestamp = int(time.time())
            payload_file = f"payloads/google_ai_payload_{timestamp}.json"
            try:
                os.makedirs("payloads", exist_ok=True)
                with open(payload_file, "w") as f:
                    json.dump(payload, f, indent=2)
                print(f"📄 Saved payload to {payload_file} (prompt: ~{len(combined_prompt)//4} tokens)")
            except OSError as e:
                print(f"⚠️ Could not save debug payload to {payload_file}: {e}")

        # Large genre-mix prompts need more time than short This Is requests
        base_timeout = float(os.getenv("GOOGLE_AI_TIMEOUT", "120"))
        if len(combined_prompt) > 80000:
            timeout = max(base_timeout, 180.0)
        elif len(combined_prompt) > 40000:
            timeout = max(base_timeout, 150.0)
        else:
            timeout = base_timeout

        max_retries = int(os.getenv("GOOGLE_AI_MAX_RETRIES", "4"))
        retryable_status = {429, 500, 502, 503, 504}
        last_error: Optional[Exception] = None

        for attempt in range(max_retries):
            try:
                response = await self.client.post(
                    url,
                    json=payload,
                    headers=headers,
                    timeout=timeout,
                )
                response.raise_for_status()
                result = response.json()

                if "candidates" in result and len(result["candidates"]) > 0:
                    candidate = result["candidates"][0]

                    finish_reason = candidate.get("finishReason", "")
                    if finish_reason in ["SAFETY", "RECITATION", "OTHER"]:
                        raise Exception(f"Google AI blocked the response due to: {finish_reason}")
                    if finish_reason == "MAX_TOKENS":
                        usage = result.get("usageMetadata", {})
                        print(
                            f"⚠️ Google AI finishReason=MAX_TOKENS "
                            f"(input: {usage.get('promptTokenCount', '?')}, "
                            f"output: {usage.get('candidatesTokenCount', '?')}) — parsing available text"
                        )

                    if "content" in candidate:
                        content = candidate["content"]
                        if "parts" in content and isinstance(content["parts"], list):
                            parts = content["parts"]
                            if len(parts) > 0 and "text" in parts[0]:
                                text = parts[0]["text"].strip()
                                try:
                                    parsed = json.loads(text)
                                    return json.dumps(parsed, ensure_ascii=False)
                                except json.JSONDecodeError:
                                    json_match = re.search(r"\{.*\}", text, re.DOTALL)
                                    if json_match:
                                        try:
                                            parsed = json.loads(json_match.group())
                                            return json.dumps(parsed, ensure_ascii=False)
                                        except json.JSONDecodeError:
                                            pass
                                    return text

                    raise Exception("Google AI response missing content structure")

                raise Exception("Google AI response missing candidates array")

            except httpx.HTTPStatusError as e:
                last_error = e
                if e.response.status_code in retryable_status and attempt < max_retries - 1:
                    delay = min(30, (2 ** attempt) * 2)
                    print(
                        f"🔄 Google AI HTTP {e.response.status_code}, "
                        f"retry {attempt + 1}/{max_retries - 1} in {delay}s..."
                    )
                    await asyncio.sleep(delay)
                    continue
                raise
            except httpx.TimeoutException as e:
                last_error = e
                if attempt < max_retries - 1:
                    delay = min(30, (2 ** attempt) * 2)
                    print(f"🔄 Google AI timeout, retry {attempt + 1}/{max_retries - 1} in {delay}s...")
                    await asyncio.sleep(delay)
                    continue
                raise Exception(
                    f"Google AI request timed out after {timeout}s "
                    f"(prompt ~{len(combined_prompt) // 4} tokens). "
                    f"Try a shorter playlist or increase GOOGLE_AI_TIMEOUT."
                ) from e
            except Exception as e:
                if isinstance(e, (httpx.HTTPStatusError, httpx.TimeoutException)):
                    raise
                detail = str(e).strip() or repr(e)
                raise Exception(f"Google AI error ({type(e).__name__}): {detail}") from e

        if last_error:
            raise last_error
        raise Exception("Google AI request failed after retries")

    async def close(self):
        """Close the HTTP client"""
        if hasattr(self, 'client') and self.client:
            if hasattr(self.client, 'is_closed') and not self.client.is_closed:
                await self.client.aclose()

def get_ai_provider() -> AIProvider:
    """Factory function that reads .env and returns configured provider"""
    provider_type = os.getenv("AI_PROVIDER", "openrouter")
    
    # Validate provider type
    if provider_type not in AIProviderConfig.PROVIDERS:
        available = ", ".join(AIProviderConfig.PROVIDERS.keys())
        raise ValueError(f"Unknown AI_PROVIDER: {provider_type}. Options: {available}")
    
    provider_config = AIProviderConfig.PROVIDERS[provider_type]
    
    # Check if API key is required
    api_key = os.getenv("AI_API_KEY")
    if provider_config.requires_key and not api_key:
        raise ValueError(f"{provider_type} requires AI_API_KEY. Get one at: {provider_config.signup_url}")
    
    # Get model (user override or provider default)
    model = os.getenv("AI_MODEL") or provider_config.default_model
    
    # Get base URL (allow Ollama override, use default for others)
    if provider_type == "ollama":
        base_url = os.getenv("OLLAMA_BASE_URL", provider_config.base_url)
    else:
        base_url = provider_config.base_url
    
    return AIProvider(
        provider_type=provider_type,
        api_key=api_key,
        model=model,
        base_url=base_url
    )