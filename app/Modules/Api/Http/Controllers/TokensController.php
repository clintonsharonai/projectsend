<?php

declare(strict_types=1);

namespace App\Modules\Api\Http\Controllers;

use App\Http\Controllers\Controller;
use App\Modules\Api\Auth\TokenAbilities;
use App\Modules\Audit\Action;
use App\Modules\Audit\ActivityLogger;
use App\Modules\Files\Access\StaffLibraryScope;
use App\Modules\Files\Models\Folder;
use App\Modules\Identity\Permissions\Permission;
use Illuminate\Http\JsonResponse;
use Illuminate\Http\Request;
use Illuminate\Validation\Rule;
use Laravel\Sanctum\PersonalAccessToken;

/**
 * Minting API tokens over the API — the programmatic twin of the web
 * Settings → API tokens screen.
 *
 * The web screen is the root of trust: it sits behind a session that has
 * passed login, two-factor and a fresh password confirmation. This endpoint
 * exists for an integration that already holds a credential and needs to mint
 * short-lived, scoped tokens on the owner's behalf — for example a
 * diagnostics pipeline that hands each customer a throwaway upload token.
 *
 * The gate is `token-can:create_api_tokens` (see routes/api.php): the calling
 * token must have been granted that ability AND its owner must still hold the
 * permission today. A minted token can never carry `create_api_tokens`
 * itself, so a leaked customer token cannot mint further tokens — the chain
 * stops at the issuer.
 *
 * The minted token belongs to the calling user (the owner), exactly as on the
 * web screen: the customer is the holder of the secret, not a separate
 * account.
 */
class TokensController extends Controller
{
    public function __construct(
        private readonly TokenAbilities $abilities,
        private readonly ActivityLogger $activity,
        private readonly StaffLibraryScope $scope,
    ) {}

    /**
     * Create a new API token for the calling user.
     *
     * The plaintext secret is returned once, in this response, and never
     * stored — the database holds a SHA-256 hash, as on the web screen. The
     * minted token may carry any ability the caller holds, except
     * `create_api_tokens` itself.
     *
     * Pass `folder_id` to bind the token to a single folder: the upload path
     * then forces every file it stores into that folder, ignoring any
     * `folder_id` the caller sends at upload time. This is the programmatic
     * twin of the customer-tokens screen — pair it with the `upload_only`
     * ability for a token that can only push files into one folder.
     */
    public function store(Request $request): JsonResponse
    {
        $user = $request->user();
        assert($user !== null);

        // The ceiling on what a minted token may do is the issuer's own
        // permission set at this moment — the same rule the web screen
        // applies. EnsureTokenCan re-checks the intersection on every request
        // in case the role changes later. `create_api_tokens` is removed so a
        // minted token can never mint tokens of its own.
        $grantable = array_values(array_diff(
            $this->abilities->availableFor($user),
            [Permission::CreateApiTokens->value],
        ));

        $maxDays = (int) config('api.tokens.max_days');

        $validated = $request->validate([
            'name' => ['required', 'string', 'max:255'],
            'abilities' => ['required', 'array', 'min:1'],
            'abilities.*' => ['string', Rule::in($grantable)],
            // Optional folder binding: a token with a folder_id is a customer
            // upload token — the API forces every upload it makes into that
            // folder. Omit it for an ordinary (unbound) token.
            'folder_id' => ['nullable', 'integer', Rule::exists('folders', 'id')],
            'never_expires' => ['boolean'],
            'expires_in_days' => [
                Rule::requiredIf(fn (): bool => ! $request->boolean('never_expires')),
                'nullable', 'integer', 'min:1', 'max:'.$maxDays,
            ],
        ]);

        // The folder must be one this issuer may actually put content into —
        // the same rule the GUI and the upload path enforce — so a token can
        // never be bound to a folder its owner cannot write to.
        $folderId = null;

        if (isset($validated['folder_id'])) {
            $folder = $this->scope->folders($user)->whereKey($validated['folder_id'])->first();
            abort_unless($folder instanceof Folder && Folder::uploadableBy($user, $folder), 422);
            $folderId = $folder->id;
        }

        $expiresAt = $request->boolean('never_expires')
            ? null
            : now()->addDays((int) $validated['expires_in_days']);

        $token = $user->createToken(
            $validated['name'],
            array_values(array_unique($validated['abilities'])),
            $expiresAt,
        );

        /** @var PersonalAccessToken $accessToken */
        $accessToken = $token->accessToken;

        if ($folderId !== null) {
            $accessToken->folder_id = $folderId;
            $accessToken->save();
        }

        $this->activity->log(Action::ApiTokenCreated, $user, context: [
            'token_name' => $validated['name'],
            'abilities' => $validated['abilities'],
            'folder_id' => $folderId,
            'expires_at' => $expiresAt?->toIso8601String(),
        ]);

        return response()->json([
            'data' => [
                'id' => (string) $accessToken->getKey(),
                'name' => $accessToken->name,
                'plain_text' => $token->plainTextToken,
                'abilities' => $accessToken->abilities ?? [],
                'folder_id' => $folderId,
                'expires_at' => $accessToken->expires_at?->toIso8601String(),
                'created_at' => $accessToken->created_at?->toIso8601String(),
            ],
        ], 201);
    }
}
