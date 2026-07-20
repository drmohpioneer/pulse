export type ApiClientConfig = {
  baseUrl: string;
};

export class ApiClient {
  constructor(readonly config: ApiClientConfig) {
    // TODO: Add typed API calls after backend contracts are accepted.
  }
}

